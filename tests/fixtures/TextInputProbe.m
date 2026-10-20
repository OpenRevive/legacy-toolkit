// Executes the shipped bridge against real iOS 6 UIKit in an isolated app.
#define OR_AUTH_TOKEN @"text-input-test-only"
#define OR_BUILD_ID @"text-input-test"
#import "legacy-uikit.m"
#import <QuartzCore/QuartzCore.h>

static void Require(BOOL ok, NSString *message) {
    if (!ok) [NSException raise:@"TextInputFailure" format:@"%@",message];
}

@interface TextInputProbe : NSObject <UIApplicationDelegate,UITextFieldDelegate,UITextViewDelegate> {
    UIWindow *window;
    UITextField *input, *secure;
    UITextView *multiline;
    UILabel *result;
    NSMutableArray *events;
    BOOL rejectBegin, rejectChange, rejectEnd;
    NSUInteger fieldChanges, fieldNotifications, fieldEnds, viewChanges, viewNotifications;
}
@end

@implementation TextInputProbe
- (NSDictionary *)tree {
    NSDictionary *tree=ORHandle(@{@"auth":OR_AUTH_TOKEN,@"op":@"tree",@"bundleId":[[NSBundle mainBundle] bundleIdentifier],@"depth":@20,@"limit":@1500,@"includeHidden":@YES});
    Require([[tree objectForKey:@"ok"] boolValue], [tree description]);return tree;
}
- (NSDictionary *)node:(NSDictionary *)tree label:(NSString *)label {
    for (NSDictionary *node in [tree objectForKey:@"nodes"])
        if ([[node objectForKey:@"label"] isEqual:label] && [[node objectForKey:@"actions"] containsObject:@"set-text"]) return node;
    [NSException raise:@"TextInputFailure" format:@"Missing field %@",label];return nil;
}
- (NSDictionary *)request:(NSDictionary *)tree label:(NSString *)label action:(NSString *)action value:(id)value {
    NSDictionary *node=[self node:tree label:label];
    NSMutableDictionary *req=[NSMutableDictionary dictionaryWithDictionary:@{@"auth":OR_AUTH_TOKEN,@"op":@"action",@"bundleId":[[NSBundle mainBundle] bundleIdentifier],@"pid":[tree objectForKey:@"pid"],@"session":[tree objectForKey:@"session"],@"snapshot":[tree objectForKey:@"snapshot"],@"node":[node objectForKey:@"id"],@"class":[node objectForKey:@"class"],@"label":[node objectForKey:@"label"],@"action":action}];
    if (value) [req setObject:value forKey:@"value"];return ORHandle(req);
}
- (NSDictionary *)act:(NSString *)label action:(NSString *)action value:(id)value {
    NSDictionary *reply=[self request:[self tree] label:label action:action value:value];
    Require([[reply objectForKey:@"ok"] boolValue],[reply description]);return reply;
}
- (void)settle:(NSTimeInterval)seconds {
    NSDate *until=[NSDate dateWithTimeIntervalSinceNow:seconds];
    while ([until timeIntervalSinceNow]>0) [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:MIN(.02,[until timeIntervalSinceNow])]];
}
- (UIView *)decoration:(UIView *)view {
    NSString *name=NSStringFromClass([view class]);
    if (![view isUserInteractionEnabled] && ([name isEqual:@"UITextSelectionView"] || [name isEqual:@"UICaretView"])) return view;
    for (UIView *child in [view subviews]) {UIView *found=[self decoration:child];if (found) return found;}return nil;
}
- (NSUInteger)count:(NSString *)event {
    NSUInteger count=0;for (NSString *item in events) if ([item isEqual:event]) count++;return count;
}
- (void)changed:(id)sender {fieldChanges++;}
- (void)ended:(id)sender {fieldEnds++;}
- (void)notified:(NSNotification *)notification {
    if ([[notification name] isEqual:UITextFieldTextDidChangeNotification]) fieldNotifications++;
    else viewNotifications++;
}
- (BOOL)textFieldShouldBeginEditing:(UITextField *)field {return !rejectBegin;}
- (void)textFieldDidBeginEditing:(UITextField *)field {[events addObject:@"begin"];}
- (BOOL)textField:(UITextField *)field shouldChangeCharactersInRange:(NSRange)range replacementString:(NSString *)text {
    [events addObject:@"validate"];return !rejectChange;
}
- (BOOL)textFieldShouldEndEditing:(UITextField *)field {return !rejectEnd;}
- (void)textFieldDidEndEditing:(UITextField *)field {
    [events addObject:@"end"];
    NSString *path=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents/committed.json"];
    [[NSJSONSerialization dataWithJSONObject:@{@"text":[field text] ?: @""} options:0 error:NULL] writeToFile:path atomically:YES];
}
- (void)textViewDidBeginEditing:(UITextView *)view {[events addObject:@"view-begin"];}
- (BOOL)textView:(UITextView *)view shouldChangeTextInRange:(NSRange)range replacementText:(NSString *)text {
    [events addObject:@"view-validate"];return !rejectChange;
}
- (void)textViewDidChange:(UITextView *)view {viewChanges++;}
- (void)textViewDidEndEditing:(UITextView *)view {[events addObject:@"view-end"];}
- (void)run {
    NSMutableDictionary *checks=[NSMutableDictionary dictionary];
    NSMutableArray *keyboardMethods=[NSMutableArray array];unsigned int methodCount=0;
    Method *methods=class_copyMethodList(NSClassFromString(@"UIKeyboardImpl"),&methodCount);
    for (unsigned int i=0;i<methodCount;i++) {
        NSString *name=NSStringFromSelector(method_getName(methods[i]));
        if ([name isEqual:@"addInputString:"] || [name isEqual:@"handleDelete"])
            [keyboardMethods addObject:@{@"selector":name,@"encoding":[NSString stringWithUTF8String:method_getTypeEncoding(methods[i])]}];
    }
    free(methods);[checks setObject:keyboardMethods forKey:@"keyboardMethods"];
    @try {
        NSString *text=@"Привет 日本語 & <VK> 🦁";
        NSDictionary *initial=[self tree];
        Require(![[[self node:initial label:@"Input"] objectForKey:@"isFirstResponder"] boolValue],@"Initial field already focused");
        NSDictionary *reply=[self request:initial label:@"Input" action:@"set-text" value:text];
        Require([[reply objectForKey:@"ok"] boolValue],[reply description]);
        Require([input isFirstResponder] && [[input text] isEqual:text],@"Input did not focus and retain Unicode");
        Require([self count:@"begin"]==1 && [self count:@"validate"]==1 && fieldChanges==1 && fieldNotifications==1,@"UIKit callbacks missing or duplicated");
        Require([[reply objectForKey:@"inputMethod"] isEqual:@"uikit-keyboard"] && [[reply objectForKey:@"textMatches"] boolValue],@"Missing native input receipt");
        NSDictionary *stale=[self request:initial label:@"Input" action:@"set-text" value:@"must not appear"];
        Require([[stale objectForKey:@"error"] isEqual:@"stale-snapshot"],@"Consumed edit snapshot accepted");
        [checks setObject:@YES forKey:@"unicodeAndSingleCallbacks"];

        [self settle:.5];
        UIView *decoration=[self decoration:input];Require(decoration!=nil,@"No genuine UIKit selection decoration found");
        NSString *layout=[[OROwnerContext() objectForKey:@"layoutToken"] copy];
        CGFloat alpha=[decoration alpha];CGRect frame=[decoration frame];
        [decoration setAlpha:0];[decoration setFrame:CGRectOffset(frame,1,0)];
        Require([[OROwnerContext() objectForKey:@"layoutToken"] isEqual:layout],@"Noninteractive cursor decoration invalidated layout");
        [decoration setFrame:frame];[decoration setAlpha:alpha];
        [decoration setUserInteractionEnabled:YES];
        Require(![[OROwnerContext() objectForKey:@"layoutToken"] isEqual:layout],@"Interactive selection target was ignored");
        [decoration setUserInteractionEnabled:NO];
        [checks setObject:NSStringFromClass([decoration class]) forKey:@"realSelectionDecoration"];
        [checks setObject:@YES forKey:@"decorationChangesOnly"];
        [layout release];

        [self act:@"Input" action:@"end-editing" value:nil];
        Require(![input isFirstResponder] && [self count:@"end"]==1 && fieldEnds==1,@"End editing did not commit exactly once");
        NSString *commit=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents/committed.json"];
        NSDictionary *saved=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:commit] options:0 error:NULL];
        Require([[saved objectForKey:@"text"] isEqual:text],@"Delegate did not persist the requested value");
        [self act:@"Input" action:@"end-editing" value:nil];
        Require([self count:@"end"]==1 && fieldEnds==1,@"End editing emitted duplicate commit");
        [checks setObject:@YES forKey:@"delegateCommitAndIdempotentEnd"];

        [self act:@"Input" action:@"focus" value:nil];[self settle:.5];
        Require([[[self node:[self tree] label:@"Input"] objectForKey:@"isFirstResponder"] boolValue],@"Explicit focus was not reflected in the tree");
        NSString *focusedLayout=[[OROwnerContext() objectForKey:@"layoutToken"] copy];
        for (NSUInteger sample=0;sample<6;sample++) {
            [self settle:.15];
            Require([[OROwnerContext() objectForKey:@"layoutToken"] isEqual:focusedLayout],@"Idle focused input changed the layout token");
        }
        [focusedLayout release];[checks setObject:@YES forKey:@"explicitFocusAndBlink"];
        [self act:@"Input" action:@"set-text" value:@""];Require(![[input text] length],@"Selection deletion failed");
        [self act:@"Input" action:@"set-text" value:@"keep"];
        rejectChange=YES;
        reply=[self request:[self tree] label:@"Input" action:@"set-text" value:@""];
        Require([[reply objectForKey:@"error"] isEqual:@"edit-not-retained"] && [[input text] isEqual:@"keep"],@"Field deletion veto bypassed");rejectChange=NO;
        rejectChange=YES;NSDictionary *rejectedTree=[self tree];
        reply=[self request:rejectedTree label:@"Input" action:@"set-text" value:@"reject"];
        Require([[reply objectForKey:@"error"] isEqual:@"edit-not-retained"] && [[input text] isEqual:@"keep"],@"Edit veto bypassed");
        stale=[self request:rejectedTree label:@"Input" action:@"set-text" value:@"reject"];
        Require([[stale objectForKey:@"error"] isEqual:@"stale-snapshot"],@"Rejected input left an actionable old snapshot");rejectChange=NO;
        rejectEnd=YES;NSUInteger oldEnds=[self count:@"end"];
        reply=[self request:[self tree] label:@"Input" action:@"end-editing" value:nil];
        Require([[reply objectForKey:@"error"] isEqual:@"end-editing-rejected"] && [input isFirstResponder] && [self count:@"end"]==oldEnds,@"End veto bypassed");
        rejectEnd=NO;[self act:@"Input" action:@"end-editing" value:nil];
        rejectBegin=YES;
        reply=[self request:[self tree] label:@"Input" action:@"set-text" value:@"reject"];
        Require([[reply objectForKey:@"error"] isEqual:@"focus-rejected"] && [[input text] isEqual:@"keep"],@"Focus veto bypassed");rejectBegin=NO;
        [checks setObject:@YES forKey:@"deleteAndDelegateVetoes"];

        NSString *lines=@"Первая 🦁\n日本語 & <two>";
        [self act:@"Multiline" action:@"set-text" value:lines];
        [self settle:.15];
        Require([multiline isFirstResponder] && [[multiline text] isEqual:lines] && viewChanges==1 && viewNotifications==1 && [self count:@"view-begin"]==1 && [self count:@"view-validate"]==1,@"Text view callbacks missing or duplicated");
        rejectChange=YES;
        reply=[self request:[self tree] label:@"Multiline" action:@"set-text" value:@"reject"];
        Require([[reply objectForKey:@"error"] isEqual:@"edit-not-retained"] && [[multiline text] isEqual:lines] && viewChanges==1 && viewNotifications==1,@"Text view veto bypassed");
        reply=[self request:[self tree] label:@"Multiline" action:@"set-text" value:@""];
        Require([[reply objectForKey:@"error"] isEqual:@"edit-not-retained"] && [[multiline text] isEqual:lines],@"Text view deletion veto bypassed");rejectChange=NO;
        [self act:@"Multiline" action:@"set-text" value:@""];
        [self settle:.15];
        Require(![[multiline text] length] && viewChanges==2 && viewNotifications==2,@"Text view deletion callbacks missing or duplicated");
        [self act:@"Multiline" action:@"end-editing" value:nil];Require([self count:@"view-end"]==1,@"Text view end callback missing");
        [checks setObject:@YES forKey:@"multilineInput"];

        [self act:@"Secure" action:@"set-text" value:@"synthetic-secret"];
        NSDictionary *secureTree=[self tree],*secureNode=[self node:secureTree label:@"Secure"];
        Require([[secureNode objectForKey:@"value"] isEqual:@""] && [[secureNode objectForKey:@"secure"] boolValue],@"Secure field leaked value");
        for (NSDictionary *node in [secureTree objectForKey:@"nodes"]) Require(![[node objectForKey:@"id"] hasPrefix:[[secureNode objectForKey:@"id"] stringByAppendingString:@"/"]],@"Secure descendants leaked");
        [self act:@"Secure" action:@"end-editing" value:nil];[checks setObject:@YES forKey:@"secureRedaction"];

        NSDictionary *movedTree=[self tree];CGRect original=[input frame];[input setFrame:CGRectOffset(original,1,0)];
        reply=[self request:movedTree label:@"Input" action:@"set-text" value:@"must not appear"];
        Require([[reply objectForKey:@"error"] isEqual:@"changed-element"],@"Moved target accepted");[input setFrame:original];
        NSDictionary *coveredTree=[self tree];NSString *uncovered=[[OROwnerContext() objectForKey:@"layoutToken"] copy];
        UIView *cover=[[[UIView alloc] initWithFrame:original] autorelease];[window.rootViewController.view addSubview:cover];
        Require(![[OROwnerContext() objectForKey:@"layoutToken"] isEqual:uncovered],@"Overlay did not invalidate layout");
        reply=[self request:coveredTree label:@"Input" action:@"set-text" value:@"must not appear"];
        Require([[reply objectForKey:@"error"] isEqual:@"occluded-element"],@"Covered target accepted");[cover removeFromSuperview];[uncovered release];
        [checks setObject:@YES forKey:@"staleMovedCoveredGuards"];
        [self act:@"Input" action:@"set-text" value:text];[self act:@"Input" action:@"end-editing" value:nil];
        [result setText:@"Saved through UIKit editing lifecycle"];
        [checks setObject:@YES forKey:@"ok"];
    } @catch (NSException *exception) {
        [checks setObject:@NO forKey:@"ok"];[checks setObject:[exception reason] ?: [exception name] forKey:@"error"];
        [result setText:[checks objectForKey:@"error"]];
    }
    [checks setObject:events forKey:@"events"];
    [checks setObject:OR_TEST_RUN_ID forKey:@"runId"];
    [checks setObject:@(getpid()) forKey:@"pid"];
    [checks setObject:[[UIDevice currentDevice] systemVersion] forKey:@"osVersion"];
    [checks setObject:[[UIDevice currentDevice] model] forKey:@"model"];
    [checks setObject:@{ @"fieldChanges":@(fieldChanges),@"fieldNotifications":@(fieldNotifications),@"fieldEnds":@(fieldEnds),@"viewChanges":@(viewChanges),@"viewNotifications":@(viewNotifications) } forKey:@"counts"];
    NSString *documents=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents"];
    [[NSJSONSerialization dataWithJSONObject:checks options:NSJSONWritingPrettyPrinted error:NULL] writeToFile:[documents stringByAppendingPathComponent:@"text-input-native.json"] atomically:YES];
    UIGraphicsBeginImageContextWithOptions([window bounds].size,NO,1);[window.layer renderInContext:UIGraphicsGetCurrentContext()];
    [UIImagePNGRepresentation(UIGraphicsGetImageFromCurrentImageContext()) writeToFile:[documents stringByAppendingPathComponent:@"text-input-native.png"] atomically:YES];UIGraphicsEndImageContext();
    NSLog(@"OPENREVIVE_TEXT_INPUT_NATIVE_RESULT %@",checks);
}
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
    events=[[NSMutableArray alloc] init];window=[[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]];
    UIViewController *controller=[[[UIViewController alloc] init] autorelease];[window setRootViewController:controller];[controller.view setBackgroundColor:[UIColor whiteColor]];
    input=[[UITextField alloc] initWithFrame:CGRectMake(20,40,280,36)];[input setBorderStyle:UITextBorderStyleRoundedRect];[input setAccessibilityLabel:@"Input"];[input setDelegate:self];[input setAutocorrectionType:UITextAutocorrectionTypeNo];
    [input addTarget:self action:@selector(changed:) forControlEvents:UIControlEventEditingChanged];[input addTarget:self action:@selector(ended:) forControlEvents:UIControlEventEditingDidEnd];[controller.view addSubview:input];
    secure=[[UITextField alloc] initWithFrame:CGRectMake(20,95,280,36)];[secure setBorderStyle:UITextBorderStyleRoundedRect];[secure setAccessibilityLabel:@"Secure"];[secure setSecureTextEntry:YES];[controller.view addSubview:secure];
    multiline=[[UITextView alloc] initWithFrame:CGRectMake(20,150,280,70)];[multiline setAccessibilityLabel:@"Multiline"];[multiline setDelegate:self];[controller.view addSubview:multiline];
    result=[[UILabel alloc] initWithFrame:CGRectMake(20,240,280,100)];[result setNumberOfLines:0];[result setText:@"Testing native input…"];[controller.view addSubview:result];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(notified:) name:UITextFieldTextDidChangeNotification object:input];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(notified:) name:UITextViewTextDidChangeNotification object:multiline];
    [window makeKeyAndVisible];[self performSelector:@selector(run) withObject:nil afterDelay:.8];return YES;
}
@end

int main(int argc,char *argv[]) {
    NSAutoreleasePool *pool=[[NSAutoreleasePool alloc] init];int status=UIApplicationMain(argc,argv,nil,@"TextInputProbe");[pool drain];return status;
}
