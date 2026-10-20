// iOS 6 SpringBoard selectors, checked at runtime; no private SDK is required.
#import <objc/runtime.h>

@interface NSObject (ORSystemSelectors)
+ (id)sharedInstance;
+ (id)sharedInstanceIfExists;
+ (id)sharedInstanceIfAvailable;
+ (id)sharedAwayController;
+ (NSArray *)allWindowsIncludingInternalWindows:(BOOL)internal onlyVisibleWindows:(BOOL)visible;
- (id)icon;
- (id)displayName;
- (id)applicationBundleID;
- (BOOL)isLocked;
- (BOOL)isActive;
- (id)visibleAlertItem;
- (BOOL)hasVisibleAlert;
- (int)currentIconListIndex;
- (id)showcaseController;
- (id)showcase;
- (int)revealMode;
- (void)handleMenuDoubleTap;
- (void)showListViewAnimated:(BOOL)animated;
- (void)hideListViewAnimated:(BOOL)animated;
- (void)dismissSwitcherAnimated:(BOOL)animated;
- (BOOL)isPasswordProtected;
- (void)unlockWithSound:(BOOL)sound;
- (void)resetIdleTimerAndUndim:(BOOL)undim;
@end
static BOOL ORIsAssistive(void) {return [[[NSBundle mainBundle] bundleIdentifier] isEqual:@"com.apple.assistivetouchd"];}
static NSString *OROwnerKind(void) {
    if (ORIsAssistive()) return @"assistive-touch";
    if ([[[NSBundle mainBundle] bundleIdentifier] isEqual:@"com.apple.springboard"]) return @"springboard";
    return [[[[NSBundle mainBundle] bundlePath] pathExtension] isEqual:@"app"] ? @"application" : @"ui-service";
}
static BOOL ORIsSystem(void) {return [[[NSBundle mainBundle] bundleIdentifier] isEqual:@"com.apple.springboard"];}
static id ORShared(NSString *name) {
    Class c=NSClassFromString(name);
    if ([c respondsToSelector:@selector(sharedInstanceIfExists)]) return [c sharedInstanceIfExists];
    if ([c respondsToSelector:@selector(sharedInstanceIfAvailable)]) return [c sharedInstanceIfAvailable];
    if ([c respondsToSelector:@selector(sharedAwayController)]) return [c sharedAwayController];
    return [c respondsToSelector:@selector(sharedInstance)] ? [c sharedInstance] : nil;
}
static NSArray *ORWindows(void) {
    if ([UIWindow respondsToSelector:@selector(allWindowsIncludingInternalWindows:onlyVisibleWindows:)])
        return [UIWindow allWindowsIncludingInternalWindows:YES onlyVisibleWindows:NO];
    return [[UIApplication sharedApplication] windows];
}
static NSDictionary *ORSystemContext(void) {
    if (!ORIsSystem()) return nil;
    id app=[UIApplication sharedApplication],icons=ORShared(@"SBIconController"),alerts=ORShared(@"SBAlertItemsController"),notifications=ORShared(@"SBBulletinListController"),ui=ORShared(@"SBUIController");
    BOOL locked=[app respondsToSelector:@selector(isLocked)] ? [app isLocked] : YES;
    id alert=[alerts respondsToSelector:@selector(visibleAlertItem)] ? [alerts visibleAlertItem] : nil;
    BOOL notification=[notifications respondsToSelector:@selector(isActive)] && [notifications isActive];
    id showcase=[ui respondsToSelector:@selector(showcaseController)] ? [ui showcaseController] : nil;
    id shown=[showcase respondsToSelector:@selector(showcase)] ? [showcase showcase] : nil;
    int reveal=[showcase respondsToSelector:@selector(revealMode)] ? [showcase revealMode] : 0;
    BOOL switching=reveal!=0 && [NSStringFromClass([shown class]) isEqual:@"SBAppSwitcherController"];
    int page=[icons respondsToSelector:@selector(currentIconListIndex)] ? [icons currentIconListIndex] : -99;
    NSMutableArray *windows=[NSMutableArray array];
    for (UIWindow *w in ORWindows()) {
        if (![w isHidden] && [w alpha]>=.01) {
            [windows addObject:[NSString stringWithFormat:@"%p:%@",w,NSStringFromClass([w class])]];
            // iOS 5 exposes the panel window but not isActive on its controller.
            if ([NSStringFromClass([w class]) isEqual:@"SBBulletinWindow"]) notification=YES;
        }
    }
    NSString *surface=locked ? @"lockscreen" : alert ? @"alert" : notification ? @"notification-center" : switching ? @"app-switcher" : @"workspace";
    NSString *token=[NSString stringWithFormat:@"%d:%p:%d:%d:%d:%@",locked,alert,notification,switching,page,[windows componentsJoinedByString:@","]];
    return [NSDictionary dictionaryWithObjectsAndKeys:token,@"token",surface,@"surface",@(locked),@"screenLocked",@(notification),@"notificationCenterVisible",@(switching),@"switcherVisible",@(page),@"iconPage",
        alert ? NSStringFromClass([alert class]) : @"",@"alertClass",[NSString stringWithFormat:@"%p",alert],@"alertIdentity",windows,@"visibleWindowIdentities",nil];
}
