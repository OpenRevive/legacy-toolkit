// Root-side discovery and snapshot broker. No new TCP listener or launch daemon.
#include <sys/sysctl.h>
#include <sys/stat.h>
#include <signal.h>
#include <math.h>

static NSString *const ORDesktopFile=@"/var/root/OpenRevive/desktop-snapshot.json";
static NSDictionary *ORDError(NSString *code,NSString *message) {
    return [NSDictionary dictionaryWithObjectsAndKeys:@NO,@"ok",code,@"error",message,@"message",nil];
}
static NSString *ORDEndpoint(id pid) {return [NSString stringWithFormat:@"org.openrevive.uikit.%d",[pid intValue]];}
static NSDictionary *ORDContext(NSString *token) {
    void *sbs=dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices",RTLD_NOW);
    BOOL (*process)(CFStringRef,pid_t *)=dlsym(sbs,"SBSProcessIDForDisplayIdentifier");
    CFStringRef (*front)(void)=dlsym(sbs,"SBSCopyFrontmostApplicationDisplayIdentifier");
    mach_port_t (*port)(void)=dlsym(sbs,"SBSSpringBoardServerPort");
    void (*locked)(mach_port_t,BOOL *,BOOL *)=dlsym(sbs,"SBGetScreenLockStatus");
    if (!process || !front || !port || !locked) return nil;
    NSDictionary *reply=ORPortRequest(@"org.openrevive.system",@{@"auth":token,@"op":@"context",@"bundleId":@"com.apple.springboard"},NULL);
    if (![[reply objectForKey:@"ok"] boolValue]) return nil;
    CFStringRef current=front();NSString *bundle=current ? [[(NSString *)current copy] autorelease] : @"";if (current) CFRelease(current);
    BOOL lock=YES,passcode=YES;locked(port(),&lock,&passcode);pid_t pid=0;
    if ([bundle length] && !process((CFStringRef)bundle,&pid)) return nil;
    NSMutableDictionary *state=[NSMutableDictionary dictionaryWithDictionary:[reply objectForKey:@"systemContext"]];
    [state setObject:[reply objectForKey:@"screenPixels"] forKey:@"screenPixels"];
    [state addEntriesFromDictionary:@{@"pid":[reply objectForKey:@"pid"],@"session":[reply objectForKey:@"session"],@"frontmostBundleId":bundle,@"frontmostPid":@(pid),@"screenLocked":@(lock),@"passcodeEnabled":@(passcode)}];
    if ([[state objectForKey:@"surface"] isEqual:@"workspace"]) [state setObject:[bundle length] ? @"application" : @"home" forKey:@"surface"];
    return state;
}
static BOOL ORDSameContext(NSDictionary *a,NSDictionary *b) {
    if (!a || !b) return NO;
    for (NSString *key in @[@"pid",@"session",@"token",@"frontmostBundleId",@"frontmostPid",@"screenLocked",@"passcodeEnabled"])
        if (![[a objectForKey:key] isEqual:[b objectForKey:key]]) return NO;
    return YES;
}
static NSDictionary *ORDScan(NSString *token,NSDictionary *context) {
    int mib[]={CTL_KERN,KERN_PROC,KERN_PROC_ALL,0};size_t size=0;
    if (sysctl(mib,4,NULL,&size,NULL,0) || size>4*1024*1024) return nil;
    size+=32*sizeof(struct kinfo_proc);struct kinfo_proc *entries=malloc(size);
    if (!entries) return nil;
    if (sysctl(mib,4,entries,&size,NULL,0)) {free(entries);return nil;}
    NSMutableArray *processes=[NSMutableArray array],*owners=[NSMutableArray array],*unavailable=[NSMutableArray array],*critical=[NSMutableArray array];
    CFAbsoluteTime started=CFAbsoluteTimeGetCurrent();BOOL bounded=NO;
    for (NSUInteger i=0;i<size/sizeof(struct kinfo_proc);i++) {
        pid_t pid=entries[i].kp_proc.p_pid;if (pid<=0) continue;
        NSString *name=[NSString stringWithUTF8String:entries[i].kp_proc.p_comm] ?: @"";
        char args[4096];size_t argsize=sizeof(args);int argvMib[]={CTL_KERN,KERN_PROCARGS2,pid};NSString *path=@"";
        if (!sysctl(argvMib,3,args,&argsize,NULL,0) && argsize>sizeof(int)) {
            size_t length=strnlen(args+sizeof(int),argsize-sizeof(int));
            if (length<argsize-sizeof(int)) path=[NSString stringWithUTF8String:args+sizeof(int)] ?: @"";
        }
        NSString *bundleHint=@"";NSRange appRange=[path rangeOfString:@".app/"];
        if (appRange.location!=NSNotFound) {
            NSString *info=[[path substringToIndex:appRange.location+4] stringByAppendingPathComponent:@"Info.plist"];
            id identifier=[[NSDictionary dictionaryWithContentsOfFile:info] objectForKey:@"CFBundleIdentifier"];
            if ([identifier isKindOfClass:[NSString class]]) bundleHint=identifier;
        }
        BOOL background=[bundleHint length] && ![bundleHint isEqual:@"com.apple.springboard"] && ![bundleHint isEqual:@"com.apple.assistivetouchd"] && pid!=[[context objectForKey:@"frontmostPid"] intValue];
        BOOL candidate=[path rangeOfString:@".app/"].location!=NSNotFound || pid==[[context objectForKey:@"frontmostPid"] intValue] || [name isEqual:@"SpringBoard"] || [name isEqual:@"assistivetouchd"];
        NSMutableDictionary *item=[NSMutableDictionary dictionaryWithObjectsAndKeys:@(pid),@"pid",name,@"name",path,@"executable",@(candidate),@"uiCandidate",@"no-endpoint",@"status",nil];
        [item setObject:@(background) forKey:@"backgroundApplication"];
        if ([bundleHint length]) [item setObject:bundleHint forKey:@"bundleId"];
        CFMessagePortRef endpoint=CFMessagePortCreateRemote(NULL,(CFStringRef)ORDEndpoint(@(pid)));
        if (endpoint) {
            CFRelease(endpoint);
            if (CFAbsoluteTimeGetCurrent()-started>8 || [owners count]>=32) {[item setObject:@"discovery-limit" forKey:@"status"];bounded=YES;}
            else {
                NSString *error=nil;NSDictionary *owner=ORPortRequestTimed(ORDEndpoint(@(pid)),@{@"auth":token,@"op":@"inspect"},&error,background ? .15 : .7);
                if ([[owner objectForKey:@"ok"] boolValue] && [[owner objectForKey:@"pid"] intValue]==pid) {
                    [owners addObject:owner];[item setObject:@"bridged" forKey:@"status"];[item setObject:[owner objectForKey:@"bundleId"] forKey:@"bundleId"];
                } else {[item setObject:@"unavailable" forKey:@"status"];[item setObject:error ?: [owner objectForKey:@"error"] ?: @"invalid-owner" forKey:@"error"];}
            }
        }
        if ([[item objectForKey:@"status"] isEqual:@"unavailable"] || [[item objectForKey:@"status"] isEqual:@"discovery-limit"]) {
            [unavailable addObject:item];if (!background) [critical addObject:item];
        }
        [processes addObject:item];
    }
    free(entries);
    return @{@"processes":processes,@"owners":owners,@"unavailableEndpoints":unavailable,@"criticalUnavailableEndpoints":critical,@"discoveryTruncated":@(bounded)};
}
static BOOL ORDEligible(NSDictionary *owner,NSDictionary *context) {
    NSString *kind=[owner objectForKey:@"ownerKind"];
    if ([kind isEqual:@"assistive-touch"] || [kind isEqual:@"springboard"]) return YES;
    if ([kind isEqual:@"ui-service"]) {
        for (NSDictionary *window in [owner objectForKey:@"windows"]) if ([[window objectForKey:@"visible"] boolValue]) return YES;
        return NO;
    }
    return [[owner objectForKey:@"pid"] isEqual:[context objectForKey:@"frontmostPid"]] && ![[context objectForKey:@"screenLocked"] boolValue] && [[context objectForKey:@"surface"] isEqual:@"application"];
}
static BOOL ORDRelevant(NSDictionary *owner,NSDictionary *context) {
    return ORDEligible(owner,context);
}
static NSDictionary *ORDOwner(NSArray *owners,id pid) {
    for (NSDictionary *owner in owners) if ([[owner objectForKey:@"pid"] isEqual:pid]) return owner;
    return nil;
}
static NSDictionary *ORDNode(NSDictionary *owner,NSString *node) {
    for (NSDictionary *item in [[owner objectForKey:@"tree"] objectForKey:@"nodes"]) if ([[item objectForKey:@"id"] isEqual:node]) return item;
    return nil;
}
static NSDictionary *ORDWindow(NSDictionary *owner,NSString *node) {
    for (NSDictionary *window in [owner objectForKey:@"windows"]) if ([[window objectForKey:@"id"] isEqual:node]) return window;
    return nil;
}
static BOOL ORDContains(NSArray *rect,double x,double y) {
    return [rect count]==4 && x>=[[rect objectAtIndex:0] doubleValue] && y>=[[rect objectAtIndex:1] doubleValue] && x<[[rect objectAtIndex:0] doubleValue]+[[rect objectAtIndex:2] doubleValue] && y<[[rect objectAtIndex:1] doubleValue]+[[rect objectAtIndex:3] doubleValue];
}
static NSString *ORDPointKey(NSArray *frame) {
    if ([frame count]!=4) return nil;
    return [NSString stringWithFormat:@"%.3f,%.3f",[[frame objectAtIndex:0] doubleValue]+[[frame objectAtIndex:2] doubleValue]/2,[[frame objectAtIndex:1] doubleValue]+[[frame objectAtIndex:3] doubleValue]/2];
}
static void ORDAnnotate(NSMutableArray *owners,NSDictionary *context,NSString *token) {
    NSMutableArray *points=[NSMutableArray array];NSMutableDictionary *indices=[NSMutableDictionary dictionary],*allHits=[NSMutableDictionary dictionary];
    for (NSMutableDictionary *owner in owners) {
        NSDictionary *tree=[owner objectForKey:@"tree"];if (!tree) continue;
        NSMutableDictionary *changed=[NSMutableDictionary dictionaryWithDictionary:tree];NSMutableArray *nodes=[NSMutableArray array];
        for (NSDictionary *source in [tree objectForKey:@"nodes"]) {
            NSMutableDictionary *node=[NSMutableDictionary dictionaryWithDictionary:source];[nodes addObject:node];
            if (!ORDEligible(owner,context) || ![[node objectForKey:@"visible"] boolValue] || ![[node objectForKey:@"locallyHittable"] boolValue]) continue;
            NSArray *frame=[node objectForKey:@"framePixels"];NSString *key=ORDPointKey(frame);if (!key) continue;
            double x=[[frame objectAtIndex:0] doubleValue]+[[frame objectAtIndex:2] doubleValue]/2,y=[[frame objectAtIndex:1] doubleValue]+[[frame objectAtIndex:3] doubleValue]/2;
            NSArray *screen=[context objectForKey:@"screenPixels"];
            if ([screen count]!=4 || x<0 || x>=[[screen objectAtIndex:2] doubleValue] || y<0 || y>=[[screen objectAtIndex:3] doubleValue]) continue;
            if (![indices objectForKey:key] && [points count]<4500) {[indices setObject:@([points count]) forKey:key];[points addObject:@[@(x),@(y)]];}
        }
        [changed setObject:nodes forKey:@"nodes"];[owner setObject:changed forKey:@"tree"];
    }
    for (NSDictionary *owner in owners) {
        if (!ORDEligible(owner,context)) continue;
        NSMutableArray *hits=[NSMutableArray array];BOOL good=YES;
        for (NSUInteger start=0;start<[points count];start+=500) {
            NSArray *batch=[points subarrayWithRange:NSMakeRange(start,MIN((NSUInteger)500,[points count]-start))];
            NSDictionary *reply=ORPortRequestTimed(ORDEndpoint([owner objectForKey:@"pid"]),@{@"auth":token,@"bundleId":[owner objectForKey:@"bundleId"],@"op":@"hit-test-batch",@"points":batch},NULL,2);
            if (![[reply objectForKey:@"ok"] boolValue] || [[reply objectForKey:@"hits"] count]!=[batch count]) {good=NO;break;}
            [hits addObjectsFromArray:[reply objectForKey:@"hits"]];
        }
        if (good) [allHits setObject:hits forKey:[owner objectForKey:@"pid"]];
    }
    for (NSDictionary *owner in owners) for (NSMutableDictionary *node in [[owner objectForKey:@"tree"] objectForKey:@"nodes"]) {
        BOOL eligible=ORDEligible(owner,context),local=[[node objectForKey:@"visible"] boolValue] && [[node objectForKey:@"locallyHittable"] boolValue];
        BOOL clear=eligible && local;NSString *reason=!eligible ? @"inactive-owner" : !local ? @"hidden-or-local-occlusion" : @"clear";
        if (eligible && ![[owner objectForKey:@"screenPixels"] isEqual:[context objectForKey:@"screenPixels"]]) {clear=NO;reason=@"unsupported-coordinate-space";}
        id index=[indices objectForKey:ORDPointKey([node objectForKey:@"framePixels"]) ?: @""];
        NSDictionary *window=ORDWindow(owner,[node objectForKey:@"windowId"]);double level=[[window objectForKey:@"windowLevel"] doubleValue];
        if (clear && !index) {clear=NO;reason=@"outside-screen-or-point-limit";}
        if (clear) for (NSDictionary *other in owners) {
            if ([[other objectForKey:@"pid"] isEqual:[owner objectForKey:@"pid"]] || !ORDEligible(other,context)) continue;
            NSArray *hits=[allHits objectForKey:[other objectForKey:@"pid"]];
            if (!hits) {clear=NO;reason=@"unknown-hit-test";break;}
            NSDictionary *hit=[hits objectAtIndex:[index unsignedIntegerValue]];double hitLevel=[[hit objectForKey:@"windowLevel"] doubleValue];
            if ([[hit objectForKey:@"hit"] boolValue] && (hitLevel>level || (hitLevel==level && [[other objectForKey:@"pid"] isEqual:[context objectForKey:@"frontmostPid"]]))) {
                clear=NO;reason=@"covered-by-process";[node setObject:[other objectForKey:@"pid"] forKey:@"blockedByPid"];break;
            }
        }
        [node setObject:@(clear) forKey:@"screenHittable"];[node setObject:reason forKey:@"occlusion"];
    }
}
// Return a conservative screen owner for this point. Window levels are usable
// across iOS 6 processes; equal app/SB levels prefer the foreground application.
static NSString *ORDBlocked(NSDictionary *target,NSDictionary *node,NSArray *owners,NSDictionary *context) {
    if (!ORDEligible(target,context)) return @"This process is not a current screen owner";
    if (![[target objectForKey:@"screenPixels"] isEqual:[context objectForKey:@"screenPixels"]]) return @"Owner uses an unsupported coordinate space (iPhone compatibility mode); use a fresh native screenshot and physical gesture";
    if (![[node objectForKey:@"locallyHittable"] boolValue]) return @"Another local view covers this node";
    NSDictionary *window=ORDWindow(target,[node objectForKey:@"windowId"]);
    if (!window) return @"Owner window is missing";
    NSArray *frame=[node objectForKey:@"framePixels"];if ([frame count]!=4) return @"Target has no frame";
    double x=[[frame objectAtIndex:0] doubleValue]+[[frame objectAtIndex:2] doubleValue]/2,y=[[frame objectAtIndex:1] doubleValue]+[[frame objectAtIndex:3] doubleValue]/2;
    double level=[[window objectForKey:@"windowLevel"] doubleValue];
    for (NSDictionary *owner in owners) {
        if ([[owner objectForKey:@"pid"] isEqual:[target objectForKey:@"pid"]] || !ORDEligible(owner,context)) continue;
        for (NSDictionary *other in [owner objectForKey:@"windows"]) {
            if (![[other objectForKey:@"visible"] boolValue] || ![[other objectForKey:@"interactive"] boolValue]) continue;
            double otherLevel=[[other objectForKey:@"windowLevel"] doubleValue];
            BOOL above=otherLevel>level || (otherLevel==level && [[owner objectForKey:@"pid"] isEqual:[context objectForKey:@"frontmostPid"]]);
            if (above && ORDContains([other objectForKey:@"framePixels"],x,y)) {
                // Fullscreen transparent overlay windows need an actual hit test.
                NSDictionary *hit=ORPortRequest(ORDEndpoint([owner objectForKey:@"pid"]),@{@"auth":[context objectForKey:@"auth"],@"op":@"hit-test",@"bundleId":[owner objectForKey:@"bundleId"],@"x":@(x),@"y":@(y)},NULL);
                if (![[hit objectForKey:@"ok"] boolValue]) return @"Cross-process hit test unavailable";
                if ([[hit objectForKey:@"hit"] boolValue] && [[hit objectForKey:@"windowLevel"] doubleValue]>=otherLevel) return [NSString stringWithFormat:@"Covered by %@",[owner objectForKey:@"bundleId"]];
            }
        }
    }
    return nil;
}
static NSDictionary *ORDDesktop(NSDictionary *request,NSString *token) {
    NSString *op=[request objectForKey:@"op"];NSDictionary *context=ORDContext(token);
    if (!context) return ORDError(@"system-unavailable",@"SpringBoard owner context unavailable");
    if ([op isEqual:@"discover"] || [op isEqual:@"tree"]) {
        double capturedAt=[[NSDate date] timeIntervalSince1970];
        NSDictionary *scan=ORDScan(token,context);if (!scan) return ORDError(@"process-list-failed",@"Cannot enumerate running processes");
        NSMutableArray *owners=[NSMutableArray array];NSMutableArray *missing=[NSMutableArray array];
        BOOL read=[op isEqual:@"tree"];
        for (NSDictionary *metadata in [scan objectForKey:@"owners"]) {
            NSMutableDictionary *owner=[NSMutableDictionary dictionaryWithDictionary:metadata];
            [owner setObject:@(ORDEligible(owner,context)) forKey:@"screenEligible"];
            if (read) {
                NSMutableDictionary *packet=[NSMutableDictionary dictionaryWithDictionary:request];[packet setObject:token forKey:@"auth"];
                [packet setObject:[owner objectForKey:@"bundleId"] forKey:@"bundleId"];[packet setObject:@"owner-read" forKey:@"scope"];
                NSDictionary *tree=ORPortRequestTimed(ORDEndpoint([owner objectForKey:@"pid"]),packet,NULL,3);
                if ([[tree objectForKey:@"ok"] boolValue]) {
                    [owner setObject:tree forKey:@"tree"];
                    // Use the inventory captured in the same main-thread turn as the tree.
                    for (NSString *key in @[@"windows",@"layoutToken",@"layoutTruncated"]) [owner setObject:[tree objectForKey:key] forKey:key];
                } else [owner setObject:tree ?: ORDError(@"timeout",@"Tree unavailable") forKey:@"treeError"];
            }
            [owners addObject:owner];
        }
        if (read) ORDAnnotate(owners,context,token);
        if ([[NSDate date] timeIntervalSince1970]-capturedAt>20) return ORDError(@"capture-time-limit",@"UI capture exceeded 20 seconds; retry after closing busy processes");
        for (NSDictionary *item in [scan objectForKey:@"processes"]) if ([[item objectForKey:@"uiCandidate"] boolValue] && ![[item objectForKey:@"status"] isEqual:@"bridged"]) [missing addObject:item];
        if (!ORDSameContext(context,ORDContext(token))) return ORDError(@"screen-changed",@"Screen owner changed during capture; retry tree --all");
        CFUUIDRef uuid=CFUUIDCreate(NULL);NSString *snapshot=[(NSString *)CFUUIDCreateString(NULL,uuid) autorelease];CFRelease(uuid);
        NSMutableDictionary *result=[NSMutableDictionary dictionaryWithDictionary:@{@"ok":@YES,@"scope":@"desktop",@"snapshot":snapshot,@"systemContext":context,@"owners":owners,@"processes":[scan objectForKey:@"processes"],@"unbridgedUICandidates":missing,@"unavailableEndpoints":[scan objectForKey:@"unavailableEndpoints"],@"criticalUnavailableEndpoints":[scan objectForKey:@"criticalUnavailableEndpoints"],@"discoveryTruncated":[scan objectForKey:@"discoveryTruncated"],@"capturedAt":@(capturedAt),@"expiresInSeconds":@30}];
        if (read) {
            NSData *data=[NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
            if ([data length]>6*1024*1024 || ![data writeToFile:ORDesktopFile atomically:YES] || chmod([ORDesktopFile fileSystemRepresentation],0600)) return ORDError(@"snapshot-store",@"Cannot store private bounded desktop snapshot");
        }
        return result;
    }
    if (![op isEqual:@"action"]) return ORDError(@"invalid-operation",@"Expected discover, tree or action");
    NSData *data=[NSData dataWithContentsOfFile:ORDesktopFile];NSDictionary *cached=[data length]>0 && [data length]<=6*1024*1024 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
    if (![cached isKindOfClass:[NSDictionary class]]) return ORDError(@"stale-desktop",@"Read a fresh tree --all; desktop cache is missing or invalid");
    if (![[cached objectForKey:@"snapshot"] isEqual:[request objectForKey:@"snapshot"]] || [[NSDate date] timeIntervalSince1970]-[[cached objectForKey:@"capturedAt"] doubleValue]>30) return ORDError(@"stale-desktop",@"Read a fresh tree --all; desktop snapshot expired or was consumed");
    if (!ORDSameContext(context,[cached objectForKey:@"systemContext"])) return ORDError(@"changed-screen",@"Screen owner/context changed; read tree --all again");
    if ([[context objectForKey:@"screenLocked"] boolValue] && [[context objectForKey:@"passcodeEnabled"] boolValue]) return ORDError(@"locked",@"Unlock the passcode-protected device before input");
    NSDictionary *scan=ORDScan(token,context);NSArray *fresh=[scan objectForKey:@"owners"],*owners=[cached objectForKey:@"owners"];
    NSUInteger freshCount=0,oldCount=0;
    for (NSDictionary *item in fresh) if (ORDRelevant(item,context)) freshCount++;
    for (NSDictionary *item in owners) if (ORDRelevant(item,context)) oldCount++;
    if (!scan || freshCount!=oldCount || [[scan objectForKey:@"criticalUnavailableEndpoints"] count] || [[scan objectForKey:@"discoveryTruncated"] boolValue]) return ORDError(@"changed-processes",@"UI process set changed or discovery is partial");
    for (NSDictionary *old in owners) {
        if (!ORDRelevant(old,context)) continue; // Inactive applications and windowless services can suspend/exit without changing the screen.
        NSDictionary *now=ORDOwner(fresh,[old objectForKey:@"pid"]);
        if (!now) return ORDError(@"changed-owner",@"UI owner exited");
        for (NSString *key in @[@"bundleId",@"session",@"buildId",@"layoutToken"])
            if (![[old objectForKey:key] isEqual:[now objectForKey:key]]) return ORDError(@"changed-layout",[NSString stringWithFormat:@"%@ changed in %@; refresh tree --all",key,[old objectForKey:@"bundleId"]]);
        if ([old objectForKey:@"treeError"] || [[old objectForKey:@"layoutTruncated"] boolValue]) return ORDError(@"partial-tree",@"A process tree/layout is unavailable or truncated; automatic input is blocked");
    }
    NSMutableSet *required=[NSMutableSet setWithObject:[context objectForKey:@"pid"]];
    if ([[context objectForKey:@"frontmostPid"] intValue]>0) [required addObject:[context objectForKey:@"frontmostPid"]];
    for (NSDictionary *process in [scan objectForKey:@"processes"]) if ([[process objectForKey:@"name"] isEqual:@"assistivetouchd"]) [required addObject:[process objectForKey:@"pid"]];
    for (id pid in required) if (![ORDOwner(owners,pid) objectForKey:@"tree"]) return ORDError(@"missing-screen-owner",@"A required screen owner has no tree; repair setup-uikit before automatic input");
    NSDictionary *owner=ORDOwner(owners,[request objectForKey:@"pid"]),*node=ORDNode(owner,[request objectForKey:@"node"]);
    if (!owner || !node || ![[node objectForKey:@"class"] isEqual:[request objectForKey:@"class"]] || ![[node objectForKey:@"label"] isEqual:[request objectForKey:@"label"]]) return ORDError(@"wrong-node",@"Desktop node/owner guard mismatch");
    NSMutableDictionary *withAuth=[NSMutableDictionary dictionaryWithDictionary:context];[withAuth setObject:token forKey:@"auth"];
    NSString *blocked=ORDBlocked(owner,node,owners,withAuth);if (blocked) return ORDError(@"occluded",blocked);
    NSString *kind=[owner objectForKey:@"ownerKind"],*action=[request objectForKey:@"action"];
    if ([kind isEqual:@"ui-service"]) return ORDError(@"unverified-owner",@"This UI service is readable but has no verified input route");
    NSString *route=[action isEqual:@"tap"] ? ([kind isEqual:@"assistive-touch"] ? @"vnc" : @"hid") : @"semantic";
    if ([kind isEqual:@"assistive-touch"] && ![action isEqual:@"tap"]) return ORDError(@"unverified-action",@"AssistiveTouch uses the verified VNC tap route");
    if (![[request objectForKey:@"route"] isEqual:route]) return ORDError(@"wrong-route",@"Requested route differs from the verified owner route");
    NSString *nativeAction=[action isEqual:@"tap"] ? @"resolve-tap" : action;
    NSDictionary *tree=[owner objectForKey:@"tree"];
    NSMutableDictionary *packet=[NSMutableDictionary dictionaryWithDictionary:@{@"op":@"action",@"auth":token,@"bundleId":[owner objectForKey:@"bundleId"],@"pid":[owner objectForKey:@"pid"],@"session":[owner objectForKey:@"session"],@"snapshot":[tree objectForKey:@"snapshot"],@"node":[node objectForKey:@"id"],@"class":[node objectForKey:@"class"],@"label":[node objectForKey:@"label"],@"action":nativeAction}];
    if ([request objectForKey:@"value"]) [packet setObject:[request objectForKey:@"value"] forKey:@"value"];
    if ([kind isEqual:@"springboard"]) [packet addEntriesFromDictionary:@{@"systemToken":[context objectForKey:@"token"],@"systemContext":context}];
    if (!ORDSameContext(context,ORDContext(token))) return ORDError(@"changed-screen",@"Screen changed before dispatch");
    NSDictionary *reply=ORPortRequest(ORDEndpoint([owner objectForKey:@"pid"]),packet,NULL);
    if (![[reply objectForKey:@"ok"] boolValue]) return reply ?: ORDError(@"owner-unavailable",@"Owner unavailable");
    unlink([ORDesktopFile fileSystemRepresentation]); // Every successful resolution consumes the whole desktop epoch.
    if ([route isEqual:@"hid"]) {
        NSArray *rect=[reply objectForKey:@"framePixels"];double x=[[rect objectAtIndex:0] doubleValue]+[[rect objectAtIndex:2] doubleValue]/2,y=[[rect objectAtIndex:1] doubleValue]+[[rect objectAtIndex:3] doubleValue]/2;
        NSArray *screen=[context objectForKey:@"screenPixels"];
        if ([screen count]!=4) return ORDError(@"unknown-display",@"SpringBoard did not report its framebuffer");
        NSMutableDictionary *gesture=[NSMutableDictionary dictionaryWithDictionary:@{@"auth":token,@"op":@"play",@"bundleId":[owner objectForKey:@"bundleId"],@"pid":[owner objectForKey:@"pid"],@"systemContext":context,@"width":[screen objectAtIndex:2],@"height":[screen objectAtIndex:3],@"frames":@[@{@"t":@0,@"contacts":@[@{@"id":@1,@"phase":@"down",@"x":@(x),@"y":@(y)}]},@{@"t":@.08,@"contacts":@[@{@"id":@1,@"phase":@"up",@"x":@(x),@"y":@(y)}]}]}];
        if ([kind isEqual:@"springboard"]) [gesture setObject:@"system" forKey:@"scope"];
        reply=ORPortRequest(@"org.openrevive.hid",gesture,NULL);
        if (!reply) return ORDError(@"hid-unavailable",@"HID unavailable after consuming snapshot; verify current screen");
    }
    NSMutableDictionary *result=[NSMutableDictionary dictionaryWithDictionary:reply];
    [result addEntriesFromDictionary:@{@"route":route,@"ownerPid":[owner objectForKey:@"pid"],@"ownerBundleId":[owner objectForKey:@"bundleId"],@"desktopConsumed":@YES,@"requiresVerification":@YES}];return result;
}
