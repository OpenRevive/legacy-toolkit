// OpenRevive iOS 5 SpringBoard / iOS 6 BackBoard bridge.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <math.h>
#include <signal.h>
#include <unistd.h>
#include <dispatch/dispatch.h>
#ifndef OR_AUTH_TOKEN
#define OR_AUTH_TOKEN @""
#endif
#ifndef OR_BUILD_ID
#define OR_BUILD_ID @"development"
#endif
#ifndef OR_WIDTH
#define OR_WIDTH 640
#define OR_HEIGHT 960
#endif
#import "legacy-context.h"

// iOS 6 armv7 ABI: IOHIDFloat is float, hand transducer is 0x23.
// API declarations and constants are documented in references/uikit.md.
typedef CFTypeRef ORHIDEvent;
typedef void (*ORHIDCallback)(void *, void *, void *, ORHIDEvent);
static ORHIDCallback SystemCallback;
static void *SystemTarget, *SystemRefcon;
static Boolean (*OriginalOpen)(void *, ORHIDCallback, void *, void *, void *);
static ORHIDEvent (*CreateHand)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t,
    float, float, float, float, float, Boolean, Boolean, uint32_t);
static ORHIDEvent (*CreateFinger)(CFAllocatorRef, uint64_t, uint32_t, uint32_t, uint32_t,
    float, float, float, float, float, Boolean, Boolean, uint32_t);
static void (*Append)(ORHIDEvent, ORHIDEvent);
static void (*SetInteger)(ORHIDEvent, uint32_t, int);
static CFStringRef (*Frontmost)(void);
static mach_port_t (*SpringboardPort)(void);
static void (*LockStatus)(mach_port_t, BOOL *, BOOL *);
static BOOL (*ProcessID)(CFStringRef,pid_t *);
static NSString *Session;
static BOOL Busy;
static BOOL InSpringBoard;
// iOS 5 GraphicsServices armv7 layouts; see references/uikit.md for provenance.
typedef struct {uint8_t index,identity,proximity;float pressure,radius;CGPoint location;void *window;} ORGSPath;
typedef struct {uint32_t type;int16_t dx,dy;float unknown[6];uint8_t flags,count8;uint16_t count;ORGSPath paths[5];} ORGSHand;
typedef struct {uint32_t type,subtype;CGPoint location,windowLocation;int context;uint64_t timestamp;void *window;uint32_t flags,sender;CFIndex infoSize;} ORGSRecord;
static void (*GSSend)(const void *,mach_port_t);
static void (*GSSendSystem)(const void *);
static uint64_t (*GSTimestamp)(void);
static NSUInteger GSActive;
static mach_port_t GSRecipient;
static BOOL GSHasRecipient;
typedef char ORGSRecordSize[(sizeof(ORGSRecord)==52)?1:-1];
typedef char ORGSPathSize[(sizeof(ORGSPath)==24)?1:-1];
typedef char ORGSHandOffset[(offsetof(ORGSHand,paths)==36)?1:-1];

@interface NSObject (ORHIDUserActivity)
+ (id)sharedInstance;
+ (id)sharedApplication;
- (void)userEventOccurred;
- (void)resetIdleTimerAndUndim:(BOOL)undim;
+ (id)serverIfRunning;
- (NSArray *)displays;
- (mach_port_t)clientPortAtPosition:(CGPoint)point;
@end

static NSDictionary *Failure(NSString *code, NSString *message) {
    return [NSDictionary dictionaryWithObjectsAndKeys:@NO,@"ok",code,@"error",message,@"message",nil];
}
static double Now(void) {
    static mach_timebase_info_data_t base;
    if (!base.denom) mach_timebase_info(&base);
    return (double)mach_absolute_time()*base.numer/base.denom/1e9;
}
static void Receive(void *target, void *refcon, void *service, ORHIDEvent event) {
    if (SystemCallback) SystemCallback(target,refcon,service,event);
}
static Boolean Open(void *system, ORHIDCallback callback, void *target, void *refcon, void *unused) {
    SystemCallback=callback;SystemTarget=target;SystemRefcon=refcon;
    return OriginalOpen(system,Receive,target,refcon,unused);
}
static BOOL Owned(NSString *bundle, int pid, NSDictionary *expected, BOOL system, BOOL strict) {
    if (!Frontmost || !SpringboardPort || !LockStatus || !ProcessID || pid<1 || kill(pid,0)!=0 || ![expected isKindOfClass:[NSDictionary class]]) return NO;
    BOOL locked=YES,passcode=YES;LockStatus(SpringboardPort(),&locked,&passcode);
    if (locked!=[[expected objectForKey:@"screenLocked"] boolValue] || passcode!=[[expected objectForKey:@"passcodeEnabled"] boolValue]) return NO;
    CFStringRef current=Frontmost();NSString *front=(NSString *)(current ?: CFSTR(""));
    BOOL same=[front isEqual:system ? [expected objectForKey:@"frontmostBundleId"] : bundle];if (current) CFRelease(current);
    if (!same) return NO;
    NSString *frontBundle=[expected objectForKey:@"frontmostBundleId"];
    if ([frontBundle length]) {pid_t currentPID=0;if (!ProcessID((CFStringRef)frontBundle,&currentPID) || currentPID!=[[expected objectForKey:@"frontmostPid"] intValue]) return NO;}
    if (!system) {pid_t actual=0;if (locked || !ProcessID((CFStringRef)bundle,&actual) || actual!=pid) return NO;}
    NSDictionary *reply=ORPortRequest(@"org.openrevive.system",[NSDictionary dictionaryWithObjectsAndKeys:OR_AUTH_TOKEN,@"auth",@"com.apple.springboard",@"bundleId",@"context",@"op",nil],NULL);
    if (![[reply objectForKey:@"ok"] boolValue] || ![[reply objectForKey:@"pid"] isEqual:[expected objectForKey:@"pid"]] || ![[reply objectForKey:@"session"] isEqual:[expected objectForKey:@"session"]]) return NO;
    NSDictionary *state=[reply objectForKey:@"systemContext"];
    if (![[state objectForKey:@"alertIdentity"] isEqual:[expected objectForKey:@"alertIdentity"]]) return NO;
    if (strict && ![[state objectForKey:@"token"] isEqual:[expected objectForKey:@"token"]]) return NO;
    if (!system && ([[state objectForKey:@"notificationCenterVisible"] boolValue] || [[state objectForKey:@"switcherVisible"] boolValue])) return NO;
    return YES;
}
static BOOL Emit(NSArray *contacts) {
    if (InSpringBoard) {
        if (!GSSend || !GSSendSystem || !GSTimestamp) return NO;
        struct {ORGSRecord record;ORGSHand hand;} event;memset(&event,0,sizeof(event));
        NSUInteger active=0,index=0;
        for (NSDictionary *point in contacts) {
            BOOL down=![[point objectForKey:@"phase"] isEqual:@"up"];if (down) active++;
            ORGSPath *path=&event.hand.paths[index++];path->index=[[point objectForKey:@"id"] intValue];path->identity=path->index+1;path->proximity=down?3:0;
            path->location=CGPointMake([[point objectForKey:@"x"] floatValue],[[point objectForKey:@"y"] floatValue]);
        }
        event.record.type=3001;event.record.windowLocation=event.hand.paths[0].location;event.record.timestamp=GSTimestamp();
        event.record.infoSize=offsetof(ORGSHand,paths)+[contacts count]*sizeof(ORGSPath);
        event.hand.type=!GSActive ? 1 : !active ? 6 : active==GSActive ? 2 : 5;
        event.hand.count=[contacts count];GSActive=active;
        dispatch_sync(dispatch_get_main_queue(),^{
            id app=[NSClassFromString(@"UIApplication") sharedApplication];
            if ([app respondsToSelector:@selector(resetIdleTimerAndUndim:)]) [app resetIdleTimerAndUndim:YES];
            if (!GSHasRecipient) {
                id server=[NSClassFromString(@"CAWindowServer") serverIfRunning];NSArray *displays=[server displays];GSRecipient=0;
                CGPoint point=event.record.windowLocation;CGPoint framebuffer=CGPointMake(point.y,OR_WIDTH-1-point.x);
                if ([displays count]) GSRecipient=[[displays objectAtIndex:0] clientPortAtPosition:framebuffer];
                GSHasRecipient=YES;
            }
            if (GSRecipient) GSSend(&event.record,GSRecipient);else GSSendSystem(&event.record);
            if (!active) {GSHasRecipient=NO;GSRecipient=0;}
        });
        return YES;
    }
    uint64_t timestamp=mach_absolute_time();
    ORHIDEvent hand=CreateHand(NULL,timestamp,35,0,1,0,0,0,0,0,0,0,false,false,0);
    if (!hand) return NO;
    int handMask=0;BOOL anyDown=NO;
    for (NSDictionary *point in contacts) {
        NSString *phase=[point objectForKey:@"phase"];BOOL down=![phase isEqual:@"up"],move=[phase isEqual:@"move"];
        uint32_t index=[[point objectForKey:@"id"] intValue];
        float x=[[point objectForKey:@"x"] floatValue]/(float)OR_WIDTH,y=[[point objectForKey:@"y"] floatValue]/(float)OR_HEIGHT;
        ORHIDEvent finger=CreateFinger(NULL,timestamp,index,index+1,move?4:3,
            x,y,0,0,0,down,down,0);
        if (!finger) {CFRelease(hand);return NO;}
        Append(hand,finger);CFRelease(finger);
        handMask |= move?4:35;if (!down) handMask |= 4;anyDown |= down;
    }
    SetInteger(hand,720903,handMask);SetInteger(hand,720904,anyDown);SetInteger(hand,720905,anyDown);
    id timer=[NSClassFromString(@"BKUserEventTimer") sharedInstance];
    if ([timer respondsToSelector:@selector(userEventOccurred)]) [timer userEventOccurred];
    SystemCallback(SystemTarget,SystemRefcon,NULL,hand);
    CFRelease(hand);return YES;
}
static NSString *Validate(NSArray *frames) {
    if (![frames isKindOfClass:[NSArray class]] || [frames count]<2 || [frames count]>305) return @"Expected 2..305 frames";
    double previous=-1;NSMutableSet *active=[NSMutableSet set];NSMutableSet *used=[NSMutableSet set];
    for (NSDictionary *frame in frames) {
        if (![frame isKindOfClass:[NSDictionary class]] || ![[frame objectForKey:@"t"] isKindOfClass:[NSNumber class]]) return @"Invalid frame";
        double t=[[frame objectForKey:@"t"] doubleValue];
        if (!isfinite(t) || t<0 || t>5 || t<=previous) return @"Frame times must increase in 0..5 seconds";
        previous=t;NSArray *contacts=[frame objectForKey:@"contacts"];
        if (![contacts isKindOfClass:[NSArray class]] || ![contacts count] || [contacts count]>5) return @"Expected 1..5 contacts per frame";
        NSMutableSet *seen=[NSMutableSet set];
        for (NSDictionary *point in contacts) {
            if (![point isKindOfClass:[NSDictionary class]]) return @"Invalid contact";
            id ident=[point objectForKey:@"id"],x=[point objectForKey:@"x"],y=[point objectForKey:@"y"];
            if (![ident isKindOfClass:[NSNumber class]] || ![x isKindOfClass:[NSNumber class]] || ![y isKindOfClass:[NSNumber class]]) return @"Contact id/x/y must be numbers";
            double number=[ident doubleValue],px=[x doubleValue],py=[y doubleValue];
            if (number<1 || number>5 || floor(number)!=number || !isfinite(px) || !isfinite(py) || px<0 || px>=OR_WIDTH || py<0 || py>=OR_HEIGHT || [seen containsObject:ident]) return @"Invalid contact identity or framebuffer point";
            [seen addObject:ident];NSString *phase=[point objectForKey:@"phase"];
            if ([phase isEqual:@"down"]) {
                if ([active containsObject:ident] || [used containsObject:ident]) return @"Contact cannot start twice";
                [active addObject:ident];[used addObject:ident];
            } else if ([phase isEqual:@"move"] || [phase isEqual:@"up"]) {
                if (![active containsObject:ident]) return @"Contact moved/ended before down";
                if ([phase isEqual:@"up"]) [active removeObject:ident];
            } else return @"Invalid contact phase";
        }
        if (![active isSubsetOfSet:seen]) return @"Every active contact must be present in each frame";
    }
    return [active count] ? @"Gesture must end with every contact released" : nil;
}
static NSDictionary *ORHandle(NSDictionary *request) {
    if (![[request objectForKey:@"auth"] isEqual:OR_AUTH_TOKEN] || ![OR_AUTH_TOKEN length]) return Failure(@"unauthorized",@"Bridge authentication failed");
    if ([[request objectForKey:@"op"] isEqual:@"ping"]) return [NSDictionary dictionaryWithObjectsAndKeys:@YES,@"ok",
        [NSNumber numberWithBool:InSpringBoard ? GSSend && GSSendSystem && GSTimestamp : SystemCallback!=NULL],@"callbackReady",Session,@"session",@"0.5.0",@"bridgeVersion",
        InSpringBoard ? @"graphics-services" : @"iohid-callback",@"driver",
        OR_BUILD_ID,@"buildId",[NSNumber numberWithInt:getpid()],@"pid",[NSArray arrayWithObjects:@OR_WIDTH,@OR_HEIGHT,nil],@"framebuffer",nil];
    if (![[request objectForKey:@"op"] isEqual:@"play"]) return Failure(@"invalid-operation",@"Expected play");
    if (InSpringBoard ? !(GSSend && GSSendSystem && GSTimestamp) : !SystemCallback) return Failure(@"callback-unavailable",@"The touch bridge needs a UI restart");
    if (Busy) return Failure(@"busy",@"Another gesture is active");
    if ([[request objectForKey:@"width"] intValue]!=OR_WIDTH || [[request objectForKey:@"height"] intValue]!=OR_HEIGHT) return Failure(@"unsupported-display",@"Gesture dimensions do not match this device's portrait framebuffer");
    NSArray *frames=[request objectForKey:@"frames"];NSString *invalid=Validate(frames);
    if (invalid) return Failure(@"invalid-gesture",invalid);
    NSString *bundle=[request objectForKey:@"bundleId"];int pid=[[request objectForKey:@"pid"] intValue];
    BOOL system=[[request objectForKey:@"scope"] isEqual:@"system"];NSDictionary *context=[request objectForKey:@"systemContext"];
    if (![bundle isKindOfClass:[NSString class]] || !Owned(bundle,pid,context,system,YES)) return Failure(@"wrong-owner",@"The target app changed, exited or locked");
    NSMutableDictionary *active=[NSMutableDictionary dictionary];NSUInteger dispatched=0,maximum=0;
    NSString *error=nil;double start=Now();Busy=YES;
    @try {
        for (NSDictionary *frame in frames) {
            double deadline=start+[[frame objectForKey:@"t"] doubleValue];
            while (Now()<deadline) usleep((useconds_t)MIN(20000,MAX(1,(deadline-Now())*1e6)));
            if (!Owned(bundle,pid,context,system,NO)) {error=@"Target changed during gesture";break;}
            NSArray *contacts=[frame objectForKey:@"contacts"];
            if (!Emit(contacts)) {error=@"HID allocation failed";break;}
            for (NSDictionary *point in contacts) {
                NSNumber *ident=[point objectForKey:@"id"];
                if ([[point objectForKey:@"phase"] isEqual:@"up"]) [active removeObjectForKey:ident];
                else [active setObject:point forKey:ident];
            }
            maximum=MAX(maximum,[active count]);dispatched++;
        }
    } @catch (NSException *exception) {error=[exception name];}
    @finally {
        if ([active count]) {
            NSMutableArray *release=[NSMutableArray array];
            for (NSDictionary *point in [active allValues]) {NSMutableDictionary *up=[NSMutableDictionary dictionaryWithDictionary:point];[up setObject:@"up" forKey:@"phase"];[release addObject:up];}
            Emit(release);[active removeAllObjects];
        }
        Busy=NO;
    }
    if (error) return Failure(@"gesture-interrupted",error);
    return [NSDictionary dictionaryWithObjectsAndKeys:@YES,@"ok",@YES,@"dispatched",@YES,@"requiresVerification",
        [NSNumber numberWithUnsignedInteger:dispatched],@"frames",[NSNumber numberWithUnsignedInteger:maximum],@"maxContactsSent",
        [NSNumber numberWithDouble:Now()-start],@"elapsedSeconds",Session,@"session",nil];
}
static CFDataRef Request(CFMessagePortRef port, SInt32 message, CFDataRef bytes, void *unused) {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];NSDictionary *result;
    @try {
        id request=CFDataGetLength(bytes)<=200000 ? [NSJSONSerialization JSONObjectWithData:(NSData *)bytes options:0 error:NULL] : nil;
        result=[request isKindOfClass:[NSDictionary class]] ? ORHandle(request) : Failure(@"invalid-request",@"Expected bounded JSON object");
    } @catch (NSException *exception) {result=Failure(@"bridge-exception",[exception name]);}
    NSData *json=[NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
    CFDataRef reply=CFDataCreate(NULL,[json bytes],[json length]);[pool drain];return reply;
}
// iOS 5 hosts both UIKit and HID inside SpringBoard. Keep the gesture server on
// its own thread so context RPC and SpringBoard's main run loop stay available.
@interface ORHIDServer : NSObject
+ (void)serve:(id)unused;
@end
@implementation ORHIDServer
+ (void)serve:(id)unused {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];
    CFMessagePortRef local=CFMessagePortCreateLocal(NULL,CFSTR("org.openrevive.hid"),Request,NULL,NULL);
    if (local) {CFRunLoopSourceRef source=CFMessagePortCreateRunLoopSource(NULL,local,0);CFRunLoopAddSource(CFRunLoopGetCurrent(),source,kCFRunLoopCommonModes);CFRelease(source);CFRelease(local);CFRunLoopRun();}
    [pool drain];
}
@end
__attribute__((constructor)) static void Start(void) {
    if (![OR_AUTH_TOKEN length]) return;
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];
    // Never install this old ABI hook in another executable or iOS generation.
    NSString *process=[[NSProcessInfo processInfo] processName];
    NSDictionary *version=[NSDictionary dictionaryWithContentsOfFile:@"/System/Library/CoreServices/SystemVersion.plist"];
    BOOL ios6=[process isEqual:@"backboardd"] && [[version objectForKey:@"ProductVersion"] hasPrefix:@"6."];
    BOOL ios5=[process isEqual:@"SpringBoard"] && [[version objectForKey:@"ProductVersion"] isEqual:@"5.1.1"] && OR_WIDTH==768 && OR_HEIGHT==1024;
    if (!ios6 && !ios5) {[pool drain];return;}
    InSpringBoard=ios5;
    if (ios5) {
        void *gs=dlopen("/System/Library/PrivateFrameworks/GraphicsServices.framework/GraphicsServices",RTLD_NOW);
        GSSend=dlsym(gs,"GSSendEvent");GSSendSystem=dlsym(gs,"GSSendSystemEvent");GSTimestamp=dlsym(gs,"GSCurrentEventTimestamp");
    }
    void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW);
    void *substrate=dlopen("/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate",RTLD_NOW);
    if (!substrate) substrate=dlopen("/usr/lib/libsubstrate.dylib",RTLD_NOW);
    void (*hook)(void *, void *, void **)=dlsym(substrate,"MSHookFunction");void *open=dlsym(io,"IOHIDEventSystemOpen");
    CreateHand=dlsym(io,"IOHIDEventCreateDigitizerEvent");CreateFinger=dlsym(io,"IOHIDEventCreateDigitizerFingerEvent");
    Append=dlsym(io,"IOHIDEventAppendEvent");SetInteger=dlsym(io,"IOHIDEventSetIntegerValue");
    void *sbs=dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices",RTLD_NOW);
    Frontmost=dlsym(sbs,"SBSCopyFrontmostApplicationDisplayIdentifier");SpringboardPort=dlsym(sbs,"SBSSpringBoardServerPort");LockStatus=dlsym(sbs,"SBGetScreenLockStatus");
    ProcessID=dlsym(sbs,"SBSProcessIDForDisplayIdentifier");
    BOOL driverReady=ios5 ? GSSend && GSSendSystem && GSTimestamp : hook && open && CreateHand && CreateFinger && Append && SetInteger;
    if (driverReady && Frontmost && SpringboardPort && LockStatus && ProcessID) {
        CFUUIDRef uuid=CFUUIDCreate(NULL);Session=(NSString *)CFUUIDCreateString(NULL,uuid);CFRelease(uuid);
        if (ios6) hook(open,Open,(void **)&OriginalOpen);
        if (ios5) [NSThread detachNewThreadSelector:@selector(serve:) toTarget:[ORHIDServer class] withObject:nil];
        else {CFMessagePortRef local=CFMessagePortCreateLocal(NULL,CFSTR("org.openrevive.hid"),Request,NULL,NULL);
            if (local) {CFRunLoopSourceRef source=CFMessagePortCreateRunLoopSource(NULL,local,0);CFRunLoopAddSource(CFRunLoopGetMain(),source,kCFRunLoopCommonModes);CFRelease(source);CFRelease(local);}}
        NSLog(@"OPENREVIVE_HID_LOADED version=0.5.0 hook=%p",OriginalOpen);
    } else NSLog(@"OPENREVIVE_HID_UNAVAILABLE");
    [pool drain];
}
