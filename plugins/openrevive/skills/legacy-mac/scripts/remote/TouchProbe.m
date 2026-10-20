// iOS 5/6 UIKit sample with named controls for Instruments UI Automation.
#import <UIKit/UIKit.h>

@interface ORMultitouchPad : UIView {
    UILabel *status;
    NSMutableArray *trace;
    NSUInteger maximum, pinchEvents;
    CGFloat pinchScale;
    UIAccessibilityElement *summaryElement;
}
- (void)reset:(id)sender;
- (void)pinched:(UIPinchGestureRecognizer *)recognizer;
@end

@implementation ORMultitouchPad
- (id)initWithFrame:(CGRect)frame {
    self = [super initWithFrame:frame];
    if (self) {
        [self setMultipleTouchEnabled:YES];
        [self setBackgroundColor:[UIColor colorWithRed:0.80 green:0.88 blue:0.98 alpha:1]];
        [self setAccessibilityLabel:@"Multitouch pad"];
        [self setAccessibilityIdentifier:@"multitouch-pad"];
        trace = [[NSMutableArray alloc] init];
        summaryElement = [[UIAccessibilityElement alloc] initWithAccessibilityContainer:self];
        [summaryElement setAccessibilityLabel:@"Multitouch status"];
        status = [[UILabel alloc] initWithFrame:CGRectMake(10,10,260,85)];
        [status setBackgroundColor:[UIColor clearColor]]; [status setNumberOfLines:3];
        [self addSubview:status];
        UIPinchGestureRecognizer *pinch = [[[UIPinchGestureRecognizer alloc] initWithTarget:self action:@selector(pinched:)] autorelease];
        [pinch setCancelsTouchesInView:NO]; [self addGestureRecognizer:pinch];
        [self reset:nil];
    }
    return self;
}
- (void)write {
    [status setText:[NSString stringWithFormat:@"Max simultaneous: %lu\nPinch scale: %.3f (%lu events)",(unsigned long)maximum,pinchScale,(unsigned long)pinchEvents]];
    [summaryElement setAccessibilityValue:[status text]];
    NSDictionary *receipt = [NSDictionary dictionaryWithObjectsAndKeys:
        [NSNumber numberWithUnsignedInteger:maximum],@"maxSimultaneous",
        [NSNumber numberWithDouble:pinchScale],@"pinchScale",
        [NSNumber numberWithUnsignedInteger:pinchEvents],@"pinchEvents",trace,@"trace",nil];
    NSString *path = [NSHomeDirectory() stringByAppendingPathComponent:@"Documents/multitouch-result.json"];
    [[NSJSONSerialization dataWithJSONObject:receipt options:0 error:NULL] writeToFile:path atomically:YES];
}
- (void)reset:(id)sender { maximum = 0; pinchEvents = 0; pinchScale = 1; [trace removeAllObjects]; [self write]; }
- (void)record:(UIEvent *)event phase:(NSString *)phase {
    NSUInteger active = 0; NSMutableArray *points = [NSMutableArray array];
    for (UITouch *touch in [event allTouches]) {
        BOOL down = [touch phase] != UITouchPhaseEnded && [touch phase] != UITouchPhaseCancelled;
        if (down) active++;
        CGPoint p = [touch locationInView:self];
        [points addObject:[NSDictionary dictionaryWithObjectsAndKeys:
            [NSString stringWithFormat:@"%p",touch],@"id",[NSNumber numberWithInteger:[touch phase]],@"phase",
            [NSNumber numberWithFloat:p.x],@"x",[NSNumber numberWithFloat:p.y],@"y",nil]];
    }
    maximum = MAX(maximum,active);
    [trace addObject:[NSDictionary dictionaryWithObjectsAndKeys:phase,@"phase",points,@"touches",
        [NSNumber numberWithUnsignedInteger:active],@"active",[NSNumber numberWithDouble:[event timestamp]],@"time",nil]];
    if ([trace count] > 150) [trace removeObjectAtIndex:0];
    [self write];
}
- (void)touchesBegan:(NSSet *)touches withEvent:(UIEvent *)event { [self record:event phase:@"began"]; }
- (void)touchesMoved:(NSSet *)touches withEvent:(UIEvent *)event { [self record:event phase:@"moved"]; }
- (void)touchesEnded:(NSSet *)touches withEvent:(UIEvent *)event { [self record:event phase:@"ended"]; }
- (void)touchesCancelled:(NSSet *)touches withEvent:(UIEvent *)event { [self record:event phase:@"cancelled"]; }
- (void)pinched:(UIPinchGestureRecognizer *)recognizer { pinchScale = [recognizer scale]; pinchEvents++; [self write]; }
- (BOOL)isAccessibilityElement { return NO; }
- (NSInteger)accessibilityElementCount { return 1; }
- (id)accessibilityElementAtIndex:(NSInteger)index {
    if (index!=0) return nil;
    [summaryElement setAccessibilityFrame:[[self window] convertRect:[self convertRect:[self bounds] toView:[self window]] toWindow:nil]];
    return summaryElement;
}
- (NSInteger)indexOfAccessibilityElement:(id)element { return element==summaryElement ? 0 : NSNotFound; }
@end

@interface OpenReviveTouchProbe : NSObject <UIApplicationDelegate, UITextFieldDelegate> {
    UIWindow *window;
    UITextField *input;
    UILabel *result;
    NSUInteger count;
}
- (void)apply:(id)sender;
@end

@implementation OpenReviveTouchProbe
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    [application setIdleTimerDisabled:YES];
    window = [[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]];
    UIViewController *controller = [[[UIViewController alloc] init] autorelease];
    [controller.view setBackgroundColor:[UIColor colorWithWhite:0.94 alpha:1]];
    [window setRootViewController:controller];
    CGFloat width=MAX(280,CGRectGetWidth([[UIScreen mainScreen] bounds])-40);
    UILabel *heading = [[[UILabel alloc] initWithFrame:CGRectMake(20, 35, 280, 40)] autorelease];
    [heading setText:[NSString stringWithFormat:@"OpenRevive · iOS %@",[[UIDevice currentDevice] systemVersion]]];
    [heading setFrame:CGRectMake(20,35,width,40)];
    [heading setFont:[UIFont boldSystemFontOfSize:24]];
    [heading setBackgroundColor:[UIColor clearColor]];
    [controller.view addSubview:heading];
    input = [[UITextField alloc] initWithFrame:CGRectMake(20, 105, 280, 36)];
    [input setFrame:CGRectMake(20,105,width,36)];
    [input setBorderStyle:UITextBorderStyleRoundedRect];
    [input setPlaceholder:@"Probe text"];
    [input setAccessibilityLabel:@"Probe text"];
    [input setAutocorrectionType:UITextAutocorrectionTypeNo];
    [input setDelegate:self];
    [controller.view addSubview:input];
    UIButton *button = [UIButton buttonWithType:UIButtonTypeRoundedRect];
    [button setFrame:CGRectMake(20, 165, width, 44)];
    [button setTitle:@"Apply probe" forState:UIControlStateNormal];
    [button addTarget:self action:@selector(apply:) forControlEvents:UIControlEventTouchUpInside];
    [controller.view addSubview:button];
    result = [[UILabel alloc] initWithFrame:CGRectMake(20, 235, width, 65)];
    [result setNumberOfLines:3]; [result setText:@"Waiting for automation"];
    [result setBackgroundColor:[UIColor clearColor]];
    [result setAccessibilityLabel:@"Probe result"];
    [controller.view addSubview:result];
    ORMultitouchPad *pad = [[[ORMultitouchPad alloc] initWithFrame:CGRectMake(20,335,280,105)] autorelease];
    if (UI_USER_INTERFACE_IDIOM()==UIUserInterfaceIdiomPad) [pad setFrame:CGRectMake(20,335,width,400)];
    [controller.view addSubview:pad];
    UIButton *reset = [UIButton buttonWithType:UIButtonTypeRoundedRect];
    [reset setFrame:CGRectMake(160,300,140,28)];
    [reset setTitle:@"Reset touches" forState:UIControlStateNormal];
    [reset addTarget:pad action:@selector(reset:) forControlEvents:UIControlEventTouchUpInside];
    [controller.view addSubview:reset];
    UITextField *secure = [[[UITextField alloc] initWithFrame:CGRectMake(20,300,120,28)] autorelease];
    [secure setBorderStyle:UITextBorderStyleRoundedRect];[secure setSecureTextEntry:YES];
    [secure setAccessibilityLabel:@"Secure probe"];[secure setText:@"OR-SECURE-SENTINEL"];
    [controller.view addSubview:secure];
    [window makeKeyAndVisible];
    NSLog(@"OPENREVIVE_TOUCH_READY");
    return YES;
}
- (BOOL)textFieldShouldReturn:(UITextField *)field { [field resignFirstResponder]; return YES; }
- (void)apply:(id)sender {
    [input resignFirstResponder]; count++;
    NSString *message = [NSString stringWithFormat:@"Applied %lu: %@", (unsigned long)count, [input text]];
    [result setText:message]; [result setAccessibilityValue:message];
    NSDictionary *receipt = [NSDictionary dictionaryWithObjectsAndKeys:
        [input text] ?: @"", @"text", [NSNumber numberWithUnsignedLong:count], @"clicks", message, @"visibleResult", nil];
    NSString *path = [NSHomeDirectory() stringByAppendingPathComponent:@"Documents/probe-result.json"];
    [[NSJSONSerialization dataWithJSONObject:receipt options:0 error:NULL] writeToFile:path atomically:YES];
    NSLog(@"OPENREVIVE_TOUCH_RESULT %@", message);
}
@end

int main(int argc, char *argv[]) {
    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];
    int result = UIApplicationMain(argc, argv, nil, @"OpenReviveTouchProbe");
    [pool drain]; return result;
}
