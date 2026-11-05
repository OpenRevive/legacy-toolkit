#import <UIKit/UIKit.h>
#import <sys/sysctl.h>

@interface ORTheosProbeDelegate : UIResponder <UIApplicationDelegate> {
    UIWindow *_window;
    UILabel *_status;
    NSString *_nonce;
    NSString *_event;
    NSUInteger _taps;
}
@property(nonatomic, retain) UIWindow *window;
@end

@implementation ORTheosProbeDelegate
@synthesize window = _window;

- (UILabel *)label:(NSString *)text frame:(CGRect)frame size:(CGFloat)size {
    UILabel *label = [[[UILabel alloc] initWithFrame:frame] autorelease];
    label.backgroundColor = [UIColor clearColor];
    label.textColor = [UIColor colorWithRed:0.13 green:0.19 blue:0.25 alpha:1];
    label.font = [UIFont systemFontOfSize:size];
    label.textAlignment = NSTextAlignmentCenter;
    label.numberOfLines = 0;
    label.text = text;
    return label;
}

- (void)saveReceipt {
    char machine[64] = {0}; size_t length = sizeof(machine);
    sysctlbyname("hw.machine", machine, &length, NULL, 0);
    NSDictionary *receipt = [NSDictionary dictionaryWithObjectsAndKeys:
        @"org.openrevive.theosprobe", @"bundle_id", @"1.0.0", @"version",
        @"armv7", @"architecture", @"6.1", @"sdk", @"6.0", @"minimum_ios",
        [NSString stringWithUTF8String:machine], @"hardware",
        [[UIDevice currentDevice] systemVersion], @"ios",
        _nonce ?: @"", @"nonce", _event ?: @"launch", @"event",
        [NSNumber numberWithUnsignedInteger:_taps], @"button_taps",
        [NSNumber numberWithDouble:[[NSDate date] timeIntervalSince1970]], @"timestamp", nil];
    NSString *directory = [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES) objectAtIndex:0];
    [[NSFileManager defaultManager] createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:NULL];
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
    BOOL saved = data && [data writeToFile:[directory stringByAppendingPathComponent:@"theos-probe.json"] options:NSDataWritingAtomic error:&error];
    _status.text = saved ? [NSString stringWithFormat:@"Native app is running\nReceipt saved\nButton taps: %lu", (unsigned long)_taps] : [NSString stringWithFormat:@"Receipt failed: %@", error];
    NSLog(@"OPENREVIVE_THEOS_RECEIPT event=%@ nonce=%@ saved=%d", _event, _nonce, saved);
}

- (void)pressed:(id)sender {
    _taps++;
    [[NSUserDefaults standardUserDefaults] setInteger:_taps forKey:@"button_taps"];
    [[NSUserDefaults standardUserDefaults] synchronize];
    [_event release]; _event = [@"button" copy];
    [self saveReceipt];
}

- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    _taps = [[NSUserDefaults standardUserDefaults] integerForKey:@"button_taps"];
    self.window = [[[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]] autorelease];
    UIViewController *controller = [[[UIViewController alloc] init] autorelease];
    controller.view.backgroundColor = [UIColor colorWithRed:0.96 green:0.94 blue:0.88 alpha:1];
    CGFloat width = self.window.bounds.size.width;
    [controller.view addSubview:[self label:@"OpenRevive" frame:CGRectMake(20, 42, width-40, 30) size:20]];
    UILabel *title = [self label:@"Theos on iOS 6" frame:CGRectMake(20, 84, width-40, 48) size:29];
    title.font = [UIFont boldSystemFontOfSize:29];
    [controller.view addSubview:title];
    [controller.view addSubview:[self label:@"Built on Apple Silicon\nClang + ld64 + iOS 6.1 SDK\nARMv7 / minimum iOS 6.0" frame:CGRectMake(20, 146, width-40, 78) size:16]];
    _status = [[self label:@"Starting native probe..." frame:CGRectMake(20, 242, width-40, 92) size:19] retain];
    [controller.view addSubview:_status];
    UIButton *button = [UIButton buttonWithType:UIButtonTypeRoundedRect];
    button.frame = CGRectMake(32, 352, width-64, 48);
    [button setTitle:@"Verify native button" forState:UIControlStateNormal];
    [button addTarget:self action:@selector(pressed:) forControlEvents:UIControlEventTouchUpInside];
    [controller.view addSubview:button];
    [controller.view addSubview:[self label:@"iPhone 5 / iOS 6.1.4\nNo remote build Mac" frame:CGRectMake(20, 426, width-40, 52) size:14]];
    self.window.rootViewController = controller;
    [self.window makeKeyAndVisible];
    [self performSelector:@selector(saveReceipt) withObject:nil afterDelay:0.5];
    return YES;
}

- (BOOL)application:(UIApplication *)application openURL:(NSURL *)url sourceApplication:(NSString *)source annotation:(id)annotation {
    if (![[url scheme] isEqualToString:@"openrevive-theos"]) return NO;
    [_nonce release]; _nonce = [[url lastPathComponent] copy];
    [_event release]; _event = [@"url" copy];
    [self saveReceipt];
    return YES;
}

- (void)dealloc {
    [_window release]; [_status release]; [_nonce release]; [_event release];
    [super dealloc];
}
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([ORTheosProbeDelegate class]));
    }
}
