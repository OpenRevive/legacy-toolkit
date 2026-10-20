// Deliberately plain Cocoa controls for remote GUI verification on Lion.
#import <Cocoa/Cocoa.h>

@interface OpenReviveProbe : NSObject <NSApplicationDelegate> {
    NSWindow *window;
    NSTextField *input;
    NSTextField *result;
    NSUInteger clicks;
}
- (void)apply:(id)sender;
@end

@implementation OpenReviveProbe
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app { return YES; }
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSMenu *mainMenu = [[[NSMenu alloc] init] autorelease];
    NSMenuItem *appItem = [mainMenu addItemWithTitle:@"OpenReviveProbe" action:NULL keyEquivalent:@""];
    NSMenu *appMenu = [[[NSMenu alloc] initWithTitle:@"OpenReviveProbe"] autorelease];
    [appMenu addItemWithTitle:@"Quit OpenReviveProbe" action:@selector(terminate:) keyEquivalent:@"q"];
    [appItem setSubmenu:appMenu];
    NSMenuItem *editItem = [mainMenu addItemWithTitle:@"Edit" action:NULL keyEquivalent:@""];
    NSMenu *editMenu = [[[NSMenu alloc] initWithTitle:@"Edit"] autorelease];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    [editItem setSubmenu:editMenu];
    [NSApp setMainMenu:mainMenu];
    window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 640, 400)
        styleMask:NSTitledWindowMask | NSClosableWindowMask | NSMiniaturizableWindowMask
        backing:NSBackingStoreBuffered defer:NO];
    [window setTitle:@"OpenRevive GUI Probe"];
    [window center];
    NSTextField *heading = [[[NSTextField alloc] initWithFrame:NSMakeRect(28, 345, 580, 28)] autorelease];
    [heading setStringValue:@"Lion 10.7 · Native controls · Remote agent probe"];
    [heading setFont:[NSFont boldSystemFontOfSize:18]];
    [heading setBezeled:NO]; [heading setEditable:NO]; [heading setDrawsBackground:NO];
    [[window contentView] addSubview:heading];
    input = [[NSTextField alloc] initWithFrame:NSMakeRect(28, 280, 420, 30)];
    [input setStringValue:@"Type here over SSH"];
    [[window contentView] addSubview:input];
    NSButton *button = [[[NSButton alloc] initWithFrame:NSMakeRect(468, 276, 145, 38)] autorelease];
    [button setTitle:@"Apply probe"]; [button setBezelStyle:NSRoundedBezelStyle];
    [button setTarget:self]; [button setAction:@selector(apply:)];
    [[window contentView] addSubview:button];
    result = [[NSTextField alloc] initWithFrame:NSMakeRect(28, 185, 580, 70)];
    [result setBezeled:NO]; [result setEditable:NO]; [result setDrawsBackground:NO];
    [result setStringValue:@"Waiting for remote input and an accessibility/button click."];
    [[window contentView] addSubview:result];
    NSScrollView *scroll = [[[NSScrollView alloc] initWithFrame:NSMakeRect(28, 15, 580, 150)] autorelease];
    [scroll setHasVerticalScroller:YES]; [scroll setBorderType:NSBezelBorder];
    NSTextView *lines = [[[NSTextView alloc] initWithFrame:NSMakeRect(0, 0, 550, 1600)] autorelease];
    NSMutableString *content = [NSMutableString string];
    for (int i = 1; i <= 80; i++) [content appendFormat:@"Scroll verification line %d\n", i];
    [lines setString:content]; [lines setEditable:NO];
    [scroll setDocumentView:lines]; [[window contentView] addSubview:scroll];
    [window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
- (void)apply:(id)sender {
    clicks++;
    NSString *message = [NSString stringWithFormat:@"Applied %lu: %@", (unsigned long)clicks, [input stringValue]];
    [result setStringValue:message];
    NSDictionary *receipt = [NSDictionary dictionaryWithObjectsAndKeys:
        [input stringValue], @"text", [NSNumber numberWithUnsignedLong:clicks], @"clicks", message, @"visibleResult", nil];
    NSString *directory = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/OpenReviveProbe"];
    [[NSFileManager defaultManager] createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:NULL];
    NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:NULL];
    [data writeToFile:[directory stringByAppendingPathComponent:@"result.json"] atomically:YES];
    NSLog(@"OPENREVIVE_PROBE %@", message);
}
@end

int main(int argc, const char *argv[]) {
    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];
    NSApplication *application = [NSApplication sharedApplication];
    OpenReviveProbe *delegate = [[OpenReviveProbe alloc] init];
    [application setDelegate:delegate];
    [application run];
    [delegate release]; [pool drain]; return 0;
}
