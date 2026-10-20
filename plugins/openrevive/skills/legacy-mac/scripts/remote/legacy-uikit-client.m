#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <unistd.h>
#import "legacy-context.h"
#import "legacy-alert-probe.h"
#import "legacy-desktop.h"

static int Fail(NSString *message,int code) {fprintf(stderr,"%s\n",[message UTF8String]);return code;}
static int Output(NSDictionary *result) {
    NSData *data=[NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
    if (!data) return Fail(@"Bridge returned non-JSON data",4);
    fwrite([data bytes],1,[data length],stdout);fputc('\n',stdout);return 0;
}
int main(int argc, char **argv) {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];
    if (argc!=3) return Fail(@"Expected PORT JSON",2);
    NSString *name=[NSString stringWithUTF8String:argv[1]];
    NSData *input=[[NSString stringWithUTF8String:argv[2]] dataUsingEncoding:NSUTF8StringEncoding];
    id parsed=[input length]<=200000 ? [NSJSONSerialization JSONObjectWithData:input options:NSJSONReadingMutableContainers error:NULL] : nil;
    if (![parsed isKindOfClass:[NSMutableDictionary class]]) return Fail(@"Expected bounded JSON object",2);
    if ([name isEqual:@"probe-alert"] || [name isEqual:@"probe-wait"]) return ORAlertProbe(name,parsed,argv);
    NSString *token=[[NSString stringWithContentsOfFile:@"/var/root/OpenRevive/uikit-token" encoding:NSUTF8StringEncoding error:NULL] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (![token length]) return Fail(@"UIKit credential is missing",2);
    [parsed setObject:token forKey:@"auth"];
    if ([name isEqual:@"desktop"]) {int code=Output(ORDDesktop(parsed,token));[pool drain];return code;}
    BOOL system=[name isEqual:@"system"] || [[parsed objectForKey:@"scope"] isEqual:@"system"];
    NSString *bundle=[name hasPrefix:@"bundle:"] ? [name substringFromIndex:7] : [parsed objectForKey:@"bundleId"];
    NSDictionary *context=nil;NSString *foreground=nil;BOOL lock=YES,passcode=YES;
    BOOL (*process)(CFStringRef,pid_t *)=NULL;CFStringRef (*front)(void)=NULL;
    if (system || [bundle length]) {
        void *sbs=dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices",RTLD_NOW);
        process=dlsym(sbs,"SBSProcessIDForDisplayIdentifier");front=dlsym(sbs,"SBSCopyFrontmostApplicationDisplayIdentifier");
        mach_port_t (*springboard)(void)=dlsym(sbs,"SBSSpringBoardServerPort");
        void (*locked)(mach_port_t,BOOL *,BOOL *)=dlsym(sbs,"SBGetScreenLockStatus");
        if (!process || !front || !springboard || !locked) return Fail(@"Owner APIs unavailable",5);
        locked(springboard(),&lock,&passcode);CFStringRef current=front();foreground=current ? [[(NSString *)current copy] autorelease] : @"";if (current) CFRelease(current);
        NSString *error=nil;
        NSDictionary *reply=ORPortRequest(@"org.openrevive.system",[NSDictionary dictionaryWithObjectsAndKeys:token,@"auth",@"com.apple.springboard",@"bundleId",@"context",@"op",nil],&error);
        if (![[reply objectForKey:@"ok"] boolValue]) return Fail(error ?: @"SpringBoard context unavailable; run setup-uikit --respring",4);
        NSMutableDictionary *state=[NSMutableDictionary dictionaryWithDictionary:[reply objectForKey:@"systemContext"]];
        [state setObject:[reply objectForKey:@"screenPixels"] forKey:@"screenPixels"];
        [state setObject:[reply objectForKey:@"pid"] forKey:@"pid"];[state setObject:[reply objectForKey:@"session"] forKey:@"session"];
        [state setObject:foreground forKey:@"frontmostBundleId"];[state setObject:@(lock) forKey:@"screenLocked"];[state setObject:@(passcode) forKey:@"passcodeEnabled"];
        if ([[state objectForKey:@"surface"] isEqual:@"workspace"]) [state setObject:[foreground length] ? @"application" : @"home" forKey:@"surface"];
        pid_t frontPid=0;if ([foreground length] && !process((CFStringRef)foreground,&frontPid)) return Fail(@"Cannot resolve foreground PID",5);
        [state setObject:@(frontPid) forKey:@"frontmostPid"];context=state;
        if (system) {
            id expected=[parsed objectForKey:@"systemContext"];
            NSString *op=[parsed objectForKey:@"op"];
            BOOL read=[op isEqual:@"context"] || [op isEqual:@"ping"] || [op isEqual:@"tree"];
            if (!read) {
                if (![expected isKindOfClass:[NSDictionary class]]) return Fail(@"System actions require a fresh system snapshot",5);
                for (NSString *key in [NSArray arrayWithObjects:@"token",@"pid",@"session",@"frontmostBundleId",@"frontmostPid",@"screenLocked",@"passcodeEnabled",nil])
                    if (![[expected objectForKey:key] isEqual:[context objectForKey:key]]) return Fail(@"System context changed; read a fresh system tree",5);
                if (lock && passcode) return Fail(@"Unlock the passcode-protected device before system input",5);
            }
            if ([parsed objectForKey:@"pid"] && ![[parsed objectForKey:@"pid"] isEqual:[context objectForKey:@"pid"]]) return Fail(@"SpringBoard PID changed; read a fresh system tree",5);
            [parsed setObject:@"com.apple.springboard" forKey:@"bundleId"];[parsed setObject:[context objectForKey:@"pid"] forKey:@"pid"];
            if ([name isEqual:@"system"]) name=@"org.openrevive.system";
        } else {
            pid_t pid=0;
            if (lock || ![foreground isEqual:bundle] || !process((CFStringRef)bundle,&pid) || pid<1) return Fail(@"Target is not the unlocked foreground application",5);
            if ([parsed objectForKey:@"pid"] && [[parsed objectForKey:@"pid"] intValue]!=pid) return Fail(@"Application PID changed; read a fresh snapshot",5);
            if (![[context objectForKey:@"surface"] isEqual:@"application"]) return Fail(@"A system overlay owns the screen; read tree --system",5);
            [parsed setObject:bundle forKey:@"bundleId"];[parsed setObject:@(pid) forKey:@"pid"];
            if ([name hasPrefix:@"bundle:"]) name=[NSString stringWithFormat:@"org.openrevive.uikit.%d",pid];
        }
        [parsed setObject:context forKey:@"systemContext"];
    }
    NSString *error=nil;NSDictionary *reply=ORPortRequest(name,parsed,&error);
    if (!reply) return Fail(error ?: @"Bridge unavailable",4);
    if (system) {
        CFStringRef current=front();NSString *after=(NSString *)(current ?: CFSTR(""));BOOL same=[after isEqual:foreground];if (current) CFRelease(current);
        if ((!same || ([[reply objectForKey:@"ok"] boolValue] && ![[[reply objectForKey:@"systemContext"] objectForKey:@"token"] isEqual:[context objectForKey:@"token"]])) && [[parsed objectForKey:@"op"] isEqual:@"tree"]) return Fail(@"Foreground changed during capture; read a fresh system tree",5);
        NSMutableDictionary *result=[NSMutableDictionary dictionaryWithDictionary:reply];[result setObject:context forKey:@"systemContext"];reply=result;
    }
    int code=Output(reply);[pool drain];return code;
}
