// OpenRevive UIKit bridge: real view/accessibility objects on the main thread.
#import <UIKit/UIKit.h>
#include <unistd.h>
#include <math.h>
#import <objc/runtime.h>
#import "legacy-system.h"

#ifndef OR_AUTH_TOKEN
#define OR_AUTH_TOKEN @""
#endif
#define OR_UIKIT_VERSION @"0.5.0"
#ifndef OR_BUILD_ID
#define OR_BUILD_ID @"development"
#endif

@interface NSObject (ORAccessibilityActions)
- (BOOL)accessibilityActivate;
- (void)addInputString:(NSString *)text;
- (void)handleDelete;
@end

static NSString *Session, *Snapshot;
static BOOL Hittable(UIView *view);
static NSMutableDictionary *Objects, *Descriptions;
static CFAbsoluteTime Captured;
static NSDictionary *SnapshotContext;
static NSUInteger Limit, MaxDepth;
static BOOL Truncated, IncludeHidden;
static NSMutableSet *Visited;
static NSMutableArray *Nodes;

static NSDictionary *Error(NSString *code, NSString *message) {
    return [NSDictionary dictionaryWithObjectsAndKeys:@NO,@"ok",code,@"error",message,@"message",nil];
}
static NSString *UUID(void) {
    CFUUIDRef uuid = CFUUIDCreate(NULL);
    NSString *value = [(NSString *)CFUUIDCreateString(NULL,uuid) autorelease];
    CFRelease(uuid); return value;
}
static NSString *Text(id value, NSUInteger length) {
    if (![value isKindOfClass:[NSString class]]) return @"";
    return [value length] > length ? [value substringToIndex:length] : value;
}
static void Invalidate(void) {
    // Clear the epoch before releasing metadata, including on a reentrant call.
    id snapshot=Snapshot, objects=Objects, descriptions=Descriptions, context=SnapshotContext;
    Snapshot=nil;Objects=nil;Descriptions=nil;SnapshotContext=nil;Captured=0;
    [snapshot release];[objects release];[descriptions release];[context release];
}
static NSArray *ORRect(CGRect frame, CGFloat scale) {
    if (!isfinite(frame.origin.x) || !isfinite(frame.origin.y) || !isfinite(frame.size.width) || !isfinite(frame.size.height)) return [NSArray array];
    return [NSArray arrayWithObjects:[NSNumber numberWithDouble:frame.origin.x*scale],
        [NSNumber numberWithDouble:frame.origin.y*scale],[NSNumber numberWithDouble:frame.size.width*scale],
        [NSNumber numberWithDouble:frame.size.height*scale],nil];
}
static NSString *Role(id object) {
    if ([object isKindOfClass:NSClassFromString(@"SBIconView")]) return @"app-icon";
    if ([object isKindOfClass:[UIWindow class]]) return @"window";
    if ([object isKindOfClass:[UINavigationBar class]]) return @"navigation-bar";
    if ([object isKindOfClass:[UITextField class]]) return @"text-field";
    if ([object isKindOfClass:[UITextView class]]) return @"text-view";
    if ([object isKindOfClass:[UIButton class]] || (ORIsAssistive() && ([object isKindOfClass:NSClassFromString(@"HNDRocker")] || [object isKindOfClass:NSClassFromString(@"HNDRockerButton")]))) return @"button";
    if ([object isKindOfClass:[UISwitch class]]) return @"switch";
    if ([object isKindOfClass:[UISlider class]]) return @"slider";
    if ([object isKindOfClass:[UITableViewCell class]]) return @"table-cell";
    if ([object isKindOfClass:[UIScrollView class]]) return @"scroll-view";
    if ([object isKindOfClass:[UIWebView class]]) return @"web-view";
    if ([object isKindOfClass:[UILabel class]]) return @"label";
    return [object isKindOfClass:[UIView class]] ? @"view" : @"accessibility-element";
}
static NSDictionary *Describe(id object, BOOL hiddenAncestor) {
    BOOL view = [object isKindOfClass:[UIView class]];
    BOOL secure = [object respondsToSelector:@selector(isSecureTextEntry)] && [object isSecureTextEntry];
    BOOL hidden = hiddenAncestor, enabled = YES;
    CGRect frame = CGRectZero;
    if (view) {
        UIView *v=object;UIWindow *w=[v isKindOfClass:[UIWindow class]] ? (UIWindow *)v : [v window];
        hidden |= [v isHidden] || [v alpha] < .01 || !w;
        frame=[w convertRect:[v convertRect:[v bounds] toView:w] toWindow:nil];
        if ([v isKindOfClass:[UIControl class]]) enabled=[(UIControl *)v isEnabled];
    } else if ([object respondsToSelector:@selector(accessibilityFrame)]) frame=[object accessibilityFrame];
    NSString *label=@"", *value=@"", *identifier=@"", *labelSource=@"accessibility-or-control";
    if ([object respondsToSelector:@selector(accessibilityLabel)]) label=Text([object accessibilityLabel],512);
    if (![label length] && [object isKindOfClass:[UIButton class]]) label=Text([object currentTitle],512);
    if (![label length] && [object isKindOfClass:[UILabel class]]) label=Text([object text],512);
    if (![label length] && [object isKindOfClass:[UITextField class]]) label=Text([object placeholder],512);
    // iOS 6 draws these navigation titles without UILabel children. Derive
    // their text from the owning bar's public UINavigationItem properties.
    UINavigationBar *navigation=view && [[object superview] isKindOfClass:[UINavigationBar class]] ? (UINavigationBar *)[object superview] : nil;
    if (![label length] && navigation) {
        NSString *name=NSStringFromClass([object class]);
        if ([name isEqual:@"UINavigationItemView"]) {
            label=Text([[navigation topItem] title],512);labelSource=@"navigation-top-item";
        } else if ([name isEqual:@"UINavigationItemButtonView"] && [navigation backItem] && ![[navigation topItem] hidesBackButton] && ![[navigation topItem] leftBarButtonItem]) {
            label=Text([[[navigation backItem] backBarButtonItem] title],512);
            if (![label length]) label=Text([[navigation backItem] title],512);
            labelSource=@"navigation-back-item";
        }
    }
    if (ORIsSystem() && [object isKindOfClass:NSClassFromString(@"SBIconView")]) {
        id icon=[object icon];
        if ([icon respondsToSelector:@selector(displayName)]) {label=Text([icon displayName],512);labelSource=@"springboard-icon";}
        if ([icon respondsToSelector:@selector(applicationBundleID)]) identifier=Text([icon applicationBundleID],256);
    }
    if (![identifier length] && [object respondsToSelector:@selector(accessibilityIdentifier)]) identifier=Text([object accessibilityIdentifier],256);
    if (!secure) {
        if ([object isKindOfClass:[UITextField class]] || [object isKindOfClass:[UITextView class]] || [object isKindOfClass:[UILabel class]]) value=Text([object text],2048);
        else if ([object respondsToSelector:@selector(accessibilityValue)]) value=Text([object accessibilityValue],2048);
    }
    BOOL visible=!hidden && !CGRectIsEmpty(frame) && CGRectIntersectsRect(frame,[[UIScreen mainScreen] bounds]);
    NSMutableArray *actions=[NSMutableArray array];
    if ([object isKindOfClass:[UITextField class]] || [object isKindOfClass:[UITextView class]]) [actions addObjectsFromArray:[NSArray arrayWithObjects:@"set-text",@"focus",@"end-editing",@"return-key",nil]];
    if ([object isKindOfClass:[UIButton class]]) [actions addObject:@"press"];
    if ([object isKindOfClass:[UISwitch class]]) [actions addObject:@"set-on"];
    if ([object isKindOfClass:[UISlider class]]) [actions addObject:@"set-value"];
    if ([object isKindOfClass:[UIScrollView class]]) [actions addObject:@"scroll"];
    if ([object respondsToSelector:@selector(accessibilityActivate)]) [actions addObject:@"activate"];
    if (view) [actions addObject:@"resolve-tap"];
    NSMutableDictionary *description=[NSMutableDictionary dictionaryWithObjectsAndKeys:NSStringFromClass([object class]),@"class",Role(object),@"role",
        label,@"label",labelSource,@"labelSource",value,@"value",identifier,@"identifier",[NSNumber numberWithBool:secure],@"secure",
        [NSNumber numberWithBool:visible],@"visible",[NSNumber numberWithBool:enabled],@"enabled",
        [NSNumber numberWithBool:view && [object isUserInteractionEnabled]],@"interactive",
        ORRect(frame,1),@"framePoints",ORRect(frame,[[UIScreen mainScreen] scale]),@"framePixels",actions,@"actions",nil];
    if ([object isKindOfClass:[UIScrollView class]]) {
        UIScrollView *scroll=object;CGPoint offset=[scroll contentOffset];CGSize content=[scroll contentSize],bounds=[scroll bounds].size;UIEdgeInsets inset=[scroll contentInset];
        [description setObject:[NSArray arrayWithObjects:@(offset.x),@(offset.y),nil] forKey:@"contentOffsetPoints"];
        [description setObject:[NSArray arrayWithObjects:@(content.width),@(content.height),nil] forKey:@"contentSizePoints"];
        [description setObject:[NSArray arrayWithObjects:@(-inset.left),@(-inset.top),@(MAX(-inset.left,content.width-bounds.width+inset.right)),@(MAX(-inset.top,content.height-bounds.height+inset.bottom)),nil] forKey:@"scrollRangePoints"];
    }
    if ([object isKindOfClass:[UISwitch class]]) [description setObject:[NSNumber numberWithBool:[(UISwitch *)object isOn]] forKey:@"isOn"];
    if ([object isKindOfClass:[UITextField class]] || [object isKindOfClass:[UITextView class]]) [description setObject:@([object isFirstResponder]) forKey:@"isFirstResponder"];
    if ([object isKindOfClass:[UISlider class]]) [description setObject:[NSArray arrayWithObjects:@([(UISlider *)object minimumValue]),@([(UISlider *)object maximumValue]),nil] forKey:@"valueRange"];
    if ([object isKindOfClass:[UINavigationBar class]]) {
        [description setObject:Text([[(UINavigationBar *)object topItem] title],512) forKey:@"navigationTitle"];
        [description setObject:Text([[(UINavigationBar *)object backItem] title],512) forKey:@"backTitle"];
    }
    return description;
}
// A snapshot must not extend application view/accessibility object lifetimes.
// These records own only Foundation metadata. Reacquire the target through the
// current hierarchy before sending it any Objective-C messages during input.
static char ObjectIdentityKey;
static NSString *ObjectIdentity(id object) {
    NSString *identity=objc_getAssociatedObject(object,&ObjectIdentityKey);
    if (!identity) {
        identity=UUID();
        objc_setAssociatedObject(object,&ObjectIdentityKey,identity,OBJC_ASSOCIATION_COPY_NONATOMIC);
    }
    return identity;
}
static NSDictionary *ObjectReference(id object) {
    return [NSDictionary dictionaryWithObjectsAndKeys:
        [NSValue valueWithPointer:object],@"pointer",ObjectIdentity(object),@"generation",nil];
}
static id ObjectAtPath(NSString *path) {
    NSArray *parts=[path componentsSeparatedByString:@"/"];id current=nil;
    if (![parts count] || [parts count]>22) return nil;
    for (NSUInteger part=0;part<[parts count];part++) {
        NSString *component=[parts objectAtIndex:part];NSUInteger length=[component length],index=0;
        if (length<2 || length>6) return nil;
        for (NSUInteger i=1;i<length;i++) {
            unichar c=[component characterAtIndex:i];if (c<'0' || c>'9') return nil;
            index=index*10+c-'0';if (index>1500) return nil;
        }
        unichar kind=[component characterAtIndex:0];
        if (part==0) {
            NSArray *windows=ORWindows();if (kind!='w' || index>=[windows count]) return nil;
            current=[windows objectAtIndex:index];
        } else if (kind=='v' && [current isKindOfClass:[UIView class]]) {
            NSArray *children=[current subviews];if (index>=[children count]) return nil;
            current=[children objectAtIndex:index];
        } else if (kind=='a' && [current respondsToSelector:@selector(accessibilityElementCount)] && [current respondsToSelector:@selector(accessibilityElementAtIndex:)]) {
            @try {
                NSInteger count=[current accessibilityElementCount];
                if (count<0 || count==NSNotFound || index>=(NSUInteger)count) return nil;
                current=[current accessibilityElementAtIndex:index];
            } @catch (NSException *exception) {return nil;}
        } else return nil;
        if (!current) return nil;
    }
    return current;
}
static id SnapshotObject(NSString *path) {
    NSDictionary *reference=[Objects objectForKey:path];if (!reference) return nil;
    id current=ObjectAtPath(path);
    if (!current || (void *)current!=[[reference objectForKey:@"pointer"] pointerValue]) return nil;
    // A new object can reuse the old address. Its associated generation cannot.
    if (![objc_getAssociatedObject(current,&ObjectIdentityKey) isEqual:[reference objectForKey:@"generation"]]) return nil;
    return current;
}
static void Walk(id object, NSString *path, NSString *parent, NSUInteger depth, BOOL hiddenAncestor) {
    if (!object) return;
    NSValue *identity=[NSValue valueWithPointer:object];
    if ([Visited containsObject:identity]) return;
    if ([Nodes count] >= Limit) {Truncated=YES;return;}
    [Visited addObject:identity];
    NSDictionary *description=Describe(object,hiddenAncestor);
    NSMutableDictionary *node=[NSMutableDictionary dictionaryWithDictionary:description];
    [node setObject:path forKey:@"id"];[node setObject:parent ?: (id)[NSNull null] forKey:@"parent"];
    [Nodes addObject:node];[Objects setObject:ObjectReference(object) forKey:path];[Descriptions setObject:description forKey:path];
    // UIKit's internal labels may contain a secure field's underlying text.
    // Keep the field node and its actions, but omit its implementation subtree.
    if ([[description objectForKey:@"secure"] boolValue]) return;
    BOOL hidden=hiddenAncestor || ([object isKindOfClass:[UIView class]] && ([object isHidden] || [object alpha]<.01));
    NSArray *children=[object isKindOfClass:[UIView class]] ? [object subviews] : [NSArray array];
    NSInteger accessible=0;
    if ([object respondsToSelector:@selector(accessibilityElementCount)]) {
        @try { accessible=[object accessibilityElementCount]; } @catch (NSException *exception) { accessible=0; }
        if (accessible < 0 || accessible == NSNotFound) accessible=0;
        if ((NSUInteger)accessible>Limit) {accessible=Limit;Truncated=YES;}
    }
    if (depth >= MaxDepth) {if ([children count] || accessible) Truncated=YES;return;}
    if (hidden && !IncludeHidden) return;
    NSUInteger index=0;
    for (id child in children) {
        if ([Nodes count] >= Limit || CFAbsoluteTimeGetCurrent()-Captured>2) {Truncated=YES;break;}
        Walk(child,[path stringByAppendingFormat:@"/v%lu",(unsigned long)index++],path,depth+1,hidden);
    }
    for (NSInteger i=0;i<accessible;i++) {
        if ([Nodes count]>=Limit || CFAbsoluteTimeGetCurrent()-Captured>2) {Truncated=YES;break;}
        id child=nil;
        @try {child=[object accessibilityElementAtIndex:i];} @catch (NSException *exception) {Truncated=YES;break;}
        Walk(child,[path stringByAppendingFormat:@"/a%ld",(long)i],path,depth+1,hidden);
    }
}
static BOOL Hittable(UIView *view) {
    if (![view window]) return NO;
    CGRect frame=[[view window] convertRect:[view convertRect:[view bounds] toView:[view window]] toWindow:nil];
    CGPoint point=CGPointMake(CGRectGetMidX(frame),CGRectGetMidY(frame));
    NSArray *windows=ORWindows();
    UIWindow *best=nil; UIView *hit=nil;
    for (UIWindow *window in windows) {
        if ([window isHidden] || [window alpha]<.01) continue;
        UIView *candidate=[window hitTest:[window convertPoint:point fromWindow:nil] withEvent:nil];
        if (candidate && (!best || [window windowLevel]>=[best windowLevel])) {best=window;hit=candidate;}
    }
    if (hit==view || [hit isDescendantOfView:view]) return YES;
    UIView *interactive=view;
    while (interactive && ![interactive isUserInteractionEnabled]) interactive=[interactive superview];
    return interactive!=view && hit==interactive;
}
// A bounded structural fingerprint detects overlay/menu changes without replacing
// a retained actionable snapshot. It contains no text, including secure text.
static void ORHashView(UIView *view, uint64_t *hash, NSUInteger *count, NSUInteger depth) {
    if (++*count>1500 || depth>20) return;
    if ([view isKindOfClass:NSClassFromString(@"HNDFingerView")]) return;
    // iOS 6 blinks/repositions noninteractive text selection decoration while
    // the field, keyboard and hit targets stay unchanged. Interactive selection
    // handles, actual controls, windows and overlays still participate.
    NSString *name=NSStringFromClass([view class]);
    if (![view isUserInteractionEnabled] && ([name isEqual:@"UITextSelectionView"] || [name isEqual:@"UICaretView"])) return;
    NSString *state=[NSString stringWithFormat:@"%p:%@:%@:%d:%d:%d",view,NSStringFromClass([view class]),NSStringFromCGRect([view frame]),[view isHidden],[view alpha]<.01,[view isUserInteractionEnabled]];
    for (NSUInteger i=0;i<[state length];i++) {*hash^=[state characterAtIndex:i];*hash*=1099511628211ULL;}
    if ([view isKindOfClass:[UIWindow class]]) {
        uint64_t level=(uint64_t)((int64_t)([(UIWindow *)view windowLevel]*1000));*hash^=level;*hash*=1099511628211ULL;
    }
    // Veency's authenticated connection adds/removes a cosmetic status-bar
    // item in every app. Guard the status window itself, not those descendants.
    if ([view isKindOfClass:NSClassFromString(@"UIStatusBarWindow")] || [view isHidden] || [view alpha]<.01) return;
    for (UIView *child in [view subviews]) ORHashView(child,hash,count,depth+1);
}
static NSDictionary *OROwnerContext(void) {
    NSMutableArray *windows=[NSMutableArray array];uint64_t hash=1469598103934665603ULL;NSUInteger count=0,index=0;
    for (UIWindow *w in ORWindows()) {
        ORHashView(w,&hash,&count,0);
        NSMutableDictionary *item=[NSMutableDictionary dictionaryWithDictionary:Describe(w,NO)];
        [item setObject:[NSString stringWithFormat:@"w%lu",(unsigned long)index++] forKey:@"id"];
        [item setObject:[NSString stringWithFormat:@"%p",w] forKey:@"identity"];
        [item setObject:@([w windowLevel]) forKey:@"windowLevel"];
        [windows addObject:item];
    }
    return [NSDictionary dictionaryWithObjectsAndKeys:windows,@"windows",[NSString stringWithFormat:@"%016llx",hash],@"layoutToken",@([UIApplication sharedApplication].applicationState),@"applicationState",@(count>1500),@"layoutTruncated",nil];
}
// Closed AssistiveTouch has a fullscreen routing view: its UIKit hit test
// reports hits that are forwarded to the application. Only the rocker owns
// input until real menu buttons are visible. An open menu owns outside taps too.
static BOOL ORAssistiveMenuIn(UIView *view,NSUInteger depth) {
    if (depth>20 || [view isHidden] || [view alpha]<.01) return NO;
    if ([view isKindOfClass:NSClassFromString(@"HNDRockerButton")]) return YES;
    for (UIView *child in [view subviews]) if (ORAssistiveMenuIn(child,depth+1)) return YES;
    return NO;
}
static BOOL ORRockerAncestor(UIView *view) {
    while (view) {if ([view isKindOfClass:NSClassFromString(@"HNDRocker")]) return YES;view=[view superview];}return NO;
}
static NSDictionary *ORHandle(NSDictionary *request) {
    NSString *bundle=[[NSBundle mainBundle] bundleIdentifier] ?: @"";
    if (![[request objectForKey:@"auth"] isEqual:OR_AUTH_TOKEN] || ![OR_AUTH_TOKEN length]) return Error(@"unauthorized",@"Bridge authentication failed");
    if (![[@"ping|inspect" componentsSeparatedByString:@"|"] containsObject:[request objectForKey:@"op"]] && ![[request objectForKey:@"bundleId"] isEqual:bundle]) return Error(@"wrong-owner",@"Bundle ID changed");
    NSString *op=[request objectForKey:@"op"];
    NSMutableDictionary *owner=[NSMutableDictionary dictionaryWithObjectsAndKeys:@YES,@"ok",bundle,@"bundleId",
        [NSNumber numberWithInt:getpid()],@"pid",Session,@"session",OR_UIKIT_VERSION,@"bridgeVersion",OR_BUILD_ID,@"buildId",
        NSHomeDirectory(),@"containerPath",OROwnerKind(),@"ownerKind",ORRect([[UIScreen mainScreen] bounds],[[UIScreen mainScreen] scale]),@"screenPixels",nil];
    [owner addEntriesFromDictionary:OROwnerContext()];
    if (ORIsSystem()) {
        [owner setObject:@"system" forKey:@"scope"];
        [owner setObject:ORSystemContext() forKey:@"systemContext"];
    }
    if ([op isEqual:@"ping"] || [op isEqual:@"inspect"] || (ORIsSystem() && [op isEqual:@"context"])) return owner;
    if ([op isEqual:@"hit-test"] || [op isEqual:@"hit-test-batch"]) {
        NSArray *points=[op isEqual:@"hit-test"] ? @[@[[request objectForKey:@"x"],[request objectForKey:@"y"]]] : [request objectForKey:@"points"];
        if (![points isKindOfClass:[NSArray class]] || [points count]>500) return Error(@"invalid-points",@"Expected at most 500 framebuffer points");
        NSMutableArray *hits=[NSMutableArray array];double scale=[[UIScreen mainScreen] scale];BOOL assistiveMenu=NO;
        if (ORIsAssistive()) for (UIWindow *window in ORWindows()) if (ORAssistiveMenuIn(window,0)) {assistiveMenu=YES;break;}
        for (NSArray *pair in points) {
            if (![pair isKindOfClass:[NSArray class]] || [pair count]!=2) return Error(@"invalid-point",@"Expected [x,y]");
            double x=[[pair objectAtIndex:0] doubleValue],y=[[pair objectAtIndex:1] doubleValue];
            CGSize screen=[[UIScreen mainScreen] bounds].size;
            if (!isfinite(x) || !isfinite(y) || x<0 || y<0 || x>=screen.width*scale || y>=screen.height*scale) return Error(@"invalid-point",@"Expected framebuffer coordinates");
            UIWindow *best=nil;UIView *hit=nil;CGPoint point=CGPointMake(x/scale,y/scale);
            for (UIWindow *window in ORWindows()) {
                if ([window isHidden] || [window alpha]<.01 || ![window isUserInteractionEnabled]) continue;
                UIView *candidate=[window hitTest:[window convertPoint:point fromWindow:nil] withEvent:nil];
                if (candidate && (!best || [window windowLevel]>=[best windowLevel])) {best=window;hit=candidate;}
            }
            if (ORIsAssistive() && !assistiveMenu && !ORRockerAncestor(hit)) {best=nil;hit=nil;}
            [hits addObject:@{@"hit":@(hit!=nil),@"windowLevel":best ? @([best windowLevel]) : @(-1e9),@"hitClass":hit ? NSStringFromClass([hit class]) : @""}];
        }
        if ([op isEqual:@"hit-test"]) [owner addEntriesFromDictionary:[hits objectAtIndex:0]];
        else [owner setObject:hits forKey:@"hits"];return owner;
    }
    if (!ORIsSystem() && !ORIsAssistive() && !([op isEqual:@"tree"] && [[request objectForKey:@"scope"] isEqual:@"owner-read"]) && [[UIApplication sharedApplication] applicationState] != UIApplicationStateActive) return Error(@"inactive",@"The application is not active");
    if ([op isEqual:@"tree"]) {
        NSInteger depth=[[request objectForKey:@"depth"] integerValue], limit=[[request objectForKey:@"limit"] integerValue];
        if (depth<1 || depth>20 || limit<1 || limit>1500) return Error(@"invalid-bounds",@"depth must be 1..20 and limit 1..1500");
        Invalidate(); if (ORIsSystem()) SnapshotContext=[ORSystemContext() copy];Snapshot=[UUID() copy];Captured=CFAbsoluteTimeGetCurrent();Objects=[[NSMutableDictionary alloc] init];Descriptions=[[NSMutableDictionary alloc] init];
        Limit=limit;MaxDepth=depth;Truncated=NO;IncludeHidden=[[request objectForKey:@"includeHidden"] boolValue];
        Nodes=[NSMutableArray array];Visited=[NSMutableSet set];
        NSUInteger index=0;
        for (UIWindow *window in ORWindows()) {
            NSUInteger start=[Nodes count];NSString *windowId=[NSString stringWithFormat:@"w%lu",(unsigned long)index++];
            Walk(window,windowId,nil,0,NO);
            for (NSUInteger i=start;i<[Nodes count];i++) {
                NSMutableDictionary *node=[Nodes objectAtIndex:i];[node setObject:windowId forKey:@"windowId"];
                id object=[[node objectForKey:@"visible"] boolValue] ? SnapshotObject([node objectForKey:@"id"]) : nil;
                [node setObject:@([[node objectForKey:@"visible"] boolValue] && [object isKindOfClass:[UIView class]] && Hittable(object)) forKey:@"locallyHittable"];
            }
        }
        [owner setObject:Snapshot forKey:@"snapshot"];[owner setObject:Nodes forKey:@"nodes"];
        [owner setObject:[NSNumber numberWithBool:Truncated] forKey:@"truncated"];
        [owner setObject:[NSNumber numberWithBool:IncludeHidden] forKey:@"includesHidden"];
        [owner setObject:[NSNumber numberWithInteger:[[UIApplication sharedApplication] statusBarOrientation]] forKey:@"orientation"];
        [owner setObject:[NSNumber numberWithDouble:[[UIScreen mainScreen] scale]] forKey:@"scale"];
        [owner setObject:ORRect([[UIScreen mainScreen] bounds],[[UIScreen mainScreen] scale]) forKey:@"screenPixels"];
        [owner setObject:[NSNumber numberWithDouble:[[NSDate date] timeIntervalSince1970]] forKey:@"capturedAt"];
        [owner setObject:@30 forKey:@"expiresInSeconds"];
        [owner setObject:@2 forKey:@"traversalTimeLimitSeconds"];
        [owner setObject:[NSNumber numberWithUnsignedInteger:[Nodes count]] forKey:@"nodeCount"];
        [owner setObject:[NSNumber numberWithUnsignedInteger:MaxDepth] forKey:@"depthLimit"];
        [owner setObject:[NSNumber numberWithUnsignedInteger:Limit] forKey:@"nodeLimit"];
        [owner setObject:@"uikit-views-and-accessibility-elements" forKey:@"treeKind"];
        [owner setObject:@YES forKey:@"redactsSecureSubviews"];
        [owner addEntriesFromDictionary:OROwnerContext()];
        return owner;
    }
    BOOL validate=ORIsSystem() && [op isEqual:@"validate"],control=ORIsSystem() && [op isEqual:@"system-control"];
    if (![op isEqual:@"action"] && !validate && !control) return Error(@"invalid-operation",@"Expected tree or action");
    if (!Snapshot || ![[request objectForKey:@"snapshot"] isEqual:Snapshot] || ![[request objectForKey:@"session"] isEqual:Session] || [[request objectForKey:@"pid"] intValue]!=getpid() || CFAbsoluteTimeGetCurrent()-Captured>30) return Error(@"stale-snapshot",@"Read a fresh tree before acting");
    if (ORIsSystem() && (![[request objectForKey:@"systemToken"] isEqual:[SnapshotContext objectForKey:@"token"]] || ![[ORSystemContext() objectForKey:@"token"] isEqual:[SnapshotContext objectForKey:@"token"]])) return Error(@"changed-system-context",@"Read the system tree again after a screen change");
    if (validate || control) {
        if (control) {
            NSString *action=[request objectForKey:@"action"];id ui=ORShared(@"SBUIController"),notifications=ORShared(@"SBBulletinListController");
            if ([action isEqual:@"unlock"]) {
                id away=ORShared(@"SBAwayController");
                if (![away respondsToSelector:@selector(isPasswordProtected)] || ![away respondsToSelector:@selector(unlockWithSound:)]) return Error(@"unsupported-system-action",@"Passcode-free unlock is unavailable on this system");
                if ([away isPasswordProtected]) return Error(@"locked",@"Unlock the passcode-protected device manually");
                id application=[UIApplication sharedApplication];
                if ([application respondsToSelector:@selector(resetIdleTimerAndUndim:)]) [application resetIdleTimerAndUndim:YES];
                [away unlockWithSound:NO];
            } else if ([action isEqual:@"switcher"] && [ui respondsToSelector:@selector(handleMenuDoubleTap)]) [ui handleMenuDoubleTap];
            else if ([action isEqual:@"notifications"] && [notifications respondsToSelector:@selector(showListViewAnimated:)]) [notifications showListViewAnimated:YES];
            else if ([action isEqual:@"dismiss-notifications"] && [notifications respondsToSelector:@selector(hideListViewAnimated:)]) [notifications hideListViewAnimated:YES];
            else if ([action isEqual:@"dismiss-switcher"] && [ui respondsToSelector:@selector(dismissSwitcherAnimated:)]) [ui dismissSwitcherAnimated:YES];
            else return Error(@"unsupported-system-action",@"Expected switcher, notifications, dismiss-notifications or dismiss-switcher with a supported iOS 6 selector");
            [owner setObject:action forKey:@"action"];[owner setObject:@YES forKey:@"dispatched"];[owner setObject:@YES forKey:@"requiresVerification"];
        } else [owner setObject:@YES forKey:@"validated"];
        Invalidate();return owner;
    }
    NSString *path=[request objectForKey:@"node"];NSDictionary *before=[Descriptions objectForKey:path];
    if (!before || ![[request objectForKey:@"class"] isEqual:[before objectForKey:@"class"]] || ![[request objectForKey:@"label"] isEqual:[before objectForKey:@"label"]]) return Error(@"guard-mismatch",@"Node/class/label guard failed");
    id object=SnapshotObject(path);
    if (!object) return Error(@"changed-element",@"The snapshot target is no longer the same live UI object; read a fresh tree");
    // Description getters and app actions can change the hierarchy. Hold this
    // verified live target only for this request, never across requests.
    [[object retain] autorelease];
    NSDictionary *now=Describe(object,NO);
    if (![[now objectForKey:@"class"] isEqual:[before objectForKey:@"class"]] || ![[now objectForKey:@"label"] isEqual:[before objectForKey:@"label"]] || ![[now objectForKey:@"framePixels"] isEqual:[before objectForKey:@"framePixels"]] || ![[now objectForKey:@"visible"] boolValue] || ![[now objectForKey:@"enabled"] boolValue]) return Error(@"changed-element",@"Element visibility, identity or geometry changed");
    if ([object isKindOfClass:[UIView class]] && !Hittable(object)) return Error(@"occluded-element",@"A different view covers this element's center");
    NSString *action=[request objectForKey:@"action"];id value=[request objectForKey:@"value"];
    if (![[now objectForKey:@"actions"] containsObject:action]) return Error(@"unsupported-action",@"Use an action advertised by the current node");
    if ([action isEqual:@"set-text"]) {
        if (![value isKindOfClass:[NSString class]] || [value length]>4096) return Error(@"invalid-value",@"Text must be a string of at most 4096 UTF-16 units");
        // Route through the actual iOS 6 keyboard. Direct UITextField/UITextView
        // insertText: bypasses part of the keyboard's delegate/event pipeline.
        id keyboard=ORShared(@"UIKeyboardImpl");
        if (![keyboard respondsToSelector:@selector(addInputString:)] || ![keyboard respondsToSelector:@selector(handleDelete)]) return Error(@"unsupported-keyboard",@"The verified iOS 6 keyboard input API is unavailable");
        // Retain the target before consuming the snapshot; focus/edit delegates
        // can change the view hierarchy or reject an operation after side effects.
        Invalidate();
        if (![object isFirstResponder] && ![object becomeFirstResponder]) return Error(@"focus-rejected",@"The app refused focus; read a fresh tree");
        if (![object isFirstResponder] || ![object window] || [[UIApplication sharedApplication] applicationState]!=UIApplicationStateActive) return Error(@"focus-changed",@"The field lost focus or its active owner; read a fresh tree");
        id<UITextInput> field=object;
        UITextRange *range=[field textRangeFromPosition:[field beginningOfDocument] toPosition:[field endOfDocument]];
        if (!range) return Error(@"selection-rejected",@"The app did not provide an editable text range");
        [field setSelectedTextRange:range];
        if (![object isFirstResponder] || ![object window]) return Error(@"focus-changed",@"The field lost focus while selecting text; read a fresh tree");
        if ([value length]) [keyboard addInputString:value];
        else if ([(id<UIKeyInput>)field hasText]) [keyboard handleDelete];
        if (![[object text] isEqual:value]) return Error(@"edit-not-retained",@"The app rejected or transformed the requested text; read a fresh tree before retrying");
        [owner setObject:@YES forKey:@"textMatches"];
        [owner setObject:@([object isFirstResponder]) forKey:@"isFirstResponder"];
        [owner setObject:@"uikit-keyboard" forKey:@"inputMethod"];
    } else if ([action isEqual:@"return-key"]) {
        if (![object isFirstResponder]) return Error(@"focus-required",@"Return requires the currently focused field; read a fresh tree");
        id keyboard=ORShared(@"UIKeyboardImpl");
        if (![keyboard respondsToSelector:@selector(addInputString:)]) return Error(@"unsupported-keyboard",@"The keyboard return API is unavailable");
        Invalidate();[keyboard addInputString:@"\n"];
        [owner setObject:@"uikit-keyboard" forKey:@"inputMethod"];
    } else if ([action isEqual:@"focus"]) {
        Invalidate();
        if (![object becomeFirstResponder] || ![object isFirstResponder]) return Error(@"focus-rejected",@"The app refused focus; read a fresh tree");
        [owner setObject:@YES forKey:@"isFirstResponder"];
    } else if ([action isEqual:@"end-editing"]) {
        Invalidate();
        if ([object isFirstResponder] && ![object resignFirstResponder]) return Error(@"end-editing-rejected",@"The app refused to end editing; read a fresh tree");
        if ([object isFirstResponder]) return Error(@"end-editing-rejected",@"The field is still editing; read a fresh tree");
        [owner setObject:@NO forKey:@"isFirstResponder"];
    } else if ([action isEqual:@"press"]) [(UIControl *)object sendActionsForControlEvents:UIControlEventTouchUpInside];
    else if ([action isEqual:@"set-on"]) {
        if (![value isKindOfClass:[NSNumber class]]) return Error(@"invalid-value",@"Expected a boolean");
        [(UISwitch *)object setOn:[value boolValue] animated:NO];[(UIControl *)object sendActionsForControlEvents:UIControlEventValueChanged];
    } else if ([action isEqual:@"set-value"]) {
        float number=[value floatValue];UISlider *slider=object;
        if (![value isKindOfClass:[NSNumber class]] || !isfinite(number) || number<[slider minimumValue] || number>[slider maximumValue]) return Error(@"invalid-value",@"Slider value outside allowed range");
        [slider setValue:number];[slider sendActionsForControlEvents:UIControlEventValueChanged];
    } else if ([action isEqual:@"scroll"]) {
        if (![value isKindOfClass:[NSArray class]] || [value count]!=2) return Error(@"invalid-value",@"Expected [x,y] content offset in UIKit points");
        CGPoint offset=CGPointMake([[value objectAtIndex:0] floatValue],[[value objectAtIndex:1] floatValue]);
        UIScrollView *scroll=object;CGSize size=[scroll contentSize], bounds=[scroll bounds].size;UIEdgeInsets inset=[scroll contentInset];
        if (!isfinite(offset.x) || !isfinite(offset.y) || offset.x < -inset.left || offset.y < -inset.top || offset.x>MAX(-inset.left,size.width-bounds.width+inset.right) || offset.y>MAX(-inset.top,size.height-bounds.height+inset.bottom)) return Error(@"invalid-value",@"Content offset outside scroll range");
        [scroll setContentOffset:offset animated:NO];
    } else if ([action isEqual:@"activate"]) {
        if (![object accessibilityActivate]) return Error(@"activation-rejected",@"The accessibility element did not activate");
    } else if ([action isEqual:@"resolve-tap"]) {
        [owner setObject:[now objectForKey:@"framePixels"] forKey:@"framePixels"];
    }
    [owner setObject:action forKey:@"action"];[owner setObject:@YES forKey:@"dispatched"];
    [owner setObject:@YES forKey:@"requiresVerification"];Invalidate();return owner;
}
static CFDataRef Request(CFMessagePortRef port, SInt32 message, CFDataRef bytes, void *unused) {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];NSDictionary *result;
    @try {
        id request=CFDataGetLength(bytes)<=65536 ? [NSJSONSerialization JSONObjectWithData:(NSData *)bytes options:0 error:NULL] : nil;
        result=[request isKindOfClass:[NSDictionary class]] ? ORHandle(request) : Error(@"invalid-request",@"Expected a bounded JSON object");
    } @catch (NSException *exception) {Invalidate();result=Error(@"app-exception",[exception name]);}
    NSData *json=[NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
    CFDataRef reply=CFDataCreate(NULL,[json bytes],[json length]);[pool drain];return reply;
}
@interface ORSnapshotExpiry : NSObject
+ (void)expire:(NSTimer *)timer;
@end
@implementation ORSnapshotExpiry
+ (void)expire:(NSTimer *)timer {if (Snapshot && CFAbsoluteTimeGetCurrent()-Captured>30) Invalidate();}
@end
__attribute__((constructor)) static void Start(void) {
    if (![OR_AUTH_TOKEN length]) return;
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];
    // The UIKit Substrate filter also reaches UI services outside .app bundles.
    Session=[UUID() copy];NSString *name=[NSString stringWithFormat:@"org.openrevive.uikit.%d",getpid()];
    CFMessagePortRef port=CFMessagePortCreateLocal(NULL,(CFStringRef)name,Request,NULL,NULL);
    if (port) {
        CFRunLoopSourceRef source=CFMessagePortCreateRunLoopSource(NULL,port,0);
        CFRunLoopAddSource(CFRunLoopGetMain(),source,kCFRunLoopCommonModes);CFRelease(source);CFRelease(port);
        NSTimer *timer=[NSTimer timerWithTimeInterval:5 target:[ORSnapshotExpiry class] selector:@selector(expire:) userInfo:nil repeats:YES];
        [[NSRunLoop mainRunLoop] addTimer:timer forMode:NSRunLoopCommonModes];
    }
    if (ORIsSystem()) {
        CFMessagePortRef system=CFMessagePortCreateLocal(NULL,CFSTR("org.openrevive.system"),Request,NULL,NULL);
        if (system) {CFRunLoopSourceRef source=CFMessagePortCreateRunLoopSource(NULL,system,0);CFRunLoopAddSource(CFRunLoopGetMain(),source,kCFRunLoopCommonModes);CFRelease(source);CFRelease(system);}
    }
    NSLog(@"OPENREVIVE_UIKIT_LOADED %@ version=%@ port=%d",name,OR_UIKIT_VERSION,port!=NULL);[pool drain];
}
