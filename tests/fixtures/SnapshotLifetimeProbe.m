// Independent physical-device ownership probe. The installed Substrate bridge
// observes this app; no bridge source is linked into this executable.
#import <UIKit/UIKit.h>
#include <unistd.h>
static NSMutableDictionary *Receipt;
static void Save(void) {
    [Receipt setObject:@([[NSDate date] timeIntervalSince1970]) forKey:@"updatedAt"];
    NSString *path=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents/lifetime.json"];
    [[NSJSONSerialization dataWithJSONObject:Receipt options:0 error:NULL] writeToFile:path atomically:YES];
}
@interface LifetimeButton : UIButton
@end
@implementation LifetimeButton
- (void)dealloc {
    if ([self tag]==404) {
        [Receipt setObject:@([[Receipt objectForKey:@"deallocCount"] unsignedIntegerValue]+1) forKey:@"deallocCount"];
        Save();
    }
    [super dealloc];
}
@end
@interface SnapshotLifetimeProbe : NSObject <UIApplicationDelegate,UITextFieldDelegate,UIScrollViewDelegate> {
    UIWindow *window;
    UITextField *input, *secure;
    UIScrollView *scroll;
}
@end
@implementation SnapshotLifetimeProbe
- (void)pressed:(id)sender {
    [Receipt setObject:@([[Receipt objectForKey:@"presses"] unsignedIntegerValue]+1) forKey:@"presses"];Save();
}
- (UIButton *)button:(NSString *)title y:(CGFloat)y action:(SEL)action {
    UIButton *button=[UIButton buttonWithType:UIButtonTypeRoundedRect];
    [button setFrame:CGRectMake(20,y,280,36)];[button setTitle:title forState:UIControlStateNormal];
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    [window addSubview:button];return button;
}
- (void)replace:(NSTimer *)timer {
    UIView *old=[window viewWithTag:404];
    NSUInteger index=[[window subviews] indexOfObjectIdenticalTo:old];
    [Receipt setObject:@YES forKey:@"removed"];
    [old removeFromSuperview];
    LifetimeButton *replacement=[[LifetimeButton alloc] initWithFrame:CGRectMake(20,120,280,36)];
    [replacement setTag:405];[replacement setTitle:@"Transient target" forState:UIControlStateNormal];
    [replacement setAccessibilityLabel:@"Transient target"];
    [replacement addTarget:self action:@selector(pressed:) forControlEvents:UIControlEventTouchUpInside];
    [window insertSubview:replacement atIndex:index];[replacement release];
    [Receipt setObject:@YES forKey:@"replacementPresent"];Save();
}
- (void)arm:(id)sender {
    [Receipt setObject:@YES forKey:@"armed"];
    [Receipt setObject:@([[NSDate date] timeIntervalSince1970]+12) forKey:@"removalDueAt"];Save();
    [NSTimer scheduledTimerWithTimeInterval:12 target:self selector:@selector(replace:) userInfo:nil repeats:NO];
}
- (BOOL)textFieldShouldReturn:(UITextField *)field {[field resignFirstResponder];return YES;}
- (void)textFieldDidEndEditing:(UITextField *)field {
    [Receipt setObject:[field text] ?: @"" forKey:field==secure ? @"secureCommitted" : @"textCommitted"];Save();
}
- (void)scrollViewDidScroll:(UIScrollView *)view {[Receipt setObject:@([view contentOffset].y) forKey:@"scrollY"];Save();}
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    Receipt=[[NSMutableDictionary alloc] initWithDictionary:@{@"pid":@(getpid()),@"runId":[[NSProcessInfo processInfo] globallyUniqueString],@"deallocCount":@0,@"presses":@0,@"removed":@NO,@"armed":@NO}];
    window=[[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]];[window setBackgroundColor:[UIColor whiteColor]];
    UIViewController *controller=[[[UIViewController alloc] init] autorelease];[window setRootViewController:controller];
    [window makeKeyAndVisible];
    [self button:@"Arm removal" y:30 action:@selector(arm:)];
    [self button:@"Static press" y:75 action:@selector(pressed:)];
    LifetimeButton *target=[[LifetimeButton alloc] initWithFrame:CGRectMake(20,120,280,36)];
    [target setTag:404];[target setTitle:@"Transient target" forState:UIControlStateNormal];
    [target setAccessibilityLabel:@"Transient target"];[target addTarget:self action:@selector(pressed:) forControlEvents:UIControlEventTouchUpInside];
    [window addSubview:target];[target release];
    input=[[UITextField alloc] initWithFrame:CGRectMake(20,170,280,32)];[input setBorderStyle:UITextBorderStyleRoundedRect];
    [input setPlaceholder:@"Lifetime input"];[input setAutocorrectionType:UITextAutocorrectionTypeNo];[input setDelegate:self];[window addSubview:input];
    secure=[[UITextField alloc] initWithFrame:CGRectMake(20,210,280,32)];[secure setBorderStyle:UITextBorderStyleRoundedRect];
    [secure setPlaceholder:@"Lifetime secure"];[secure setSecureTextEntry:YES];[secure setDelegate:self];[window addSubview:secure];
    scroll=[[UIScrollView alloc] initWithFrame:CGRectMake(20,270,280,150)];[scroll setContentSize:CGSizeMake(280,600)];
    [scroll setAccessibilityLabel:@"Lifetime scroll"];[scroll setBackgroundColor:[UIColor lightGrayColor]];[scroll setDelegate:self];[window addSubview:scroll];
    UILabel *label=[[[UILabel alloc] initWithFrame:CGRectMake(10,330,250,32)] autorelease];[label setText:@"Scrolled content"];[scroll addSubview:label];
    Save();return YES;
}
@end
int main(int argc,char **argv) {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];
    int result=UIApplicationMain(argc,argv,nil,NSStringFromClass([SnapshotLifetimeProbe class]));[pool drain];return result;
}
