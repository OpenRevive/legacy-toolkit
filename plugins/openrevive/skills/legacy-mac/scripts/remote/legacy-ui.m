// Small SSH-operated GUI bridge for OS X 10.7 / Xcode 4.6 (no third-party runtime).
#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>
#import <IOKit/pwr_mgt/IOPMLib.h>
#include <unistd.h>

static void fail(NSString *message) {
    fprintf(stderr, "%s\n", [message UTF8String]);
    exit(2);
}

static void output(id object) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:&error];
    if (!data) fail([error description]);
    fwrite([data bytes], 1, [data length], stdout);
    fputc('\n', stdout);
}

static id attribute(AXUIElementRef element, CFStringRef name) {
    CFTypeRef value = NULL;
    if (AXUIElementCopyAttributeValue(element, name, &value) != kAXErrorSuccess) return nil;
    return [(id)value autorelease];
}

static NSArray *children(AXUIElementRef element) {
    id value = attribute(element, kAXChildrenAttribute);
    return [value isKindOfClass:[NSArray class]] ? value : [NSArray array];
}

static NSString *textAttribute(AXUIElementRef element, CFStringRef name) {
    id value = attribute(element, name);
    return [value isKindOfClass:[NSString class]] ? value : @"";
}

static NSDictionary *describe(AXUIElementRef element, NSString *path) {
    NSMutableDictionary *node = [NSMutableDictionary dictionaryWithObjectsAndKeys:
        path, @"path", textAttribute(element, kAXRoleAttribute), @"role",
        textAttribute(element, kAXTitleAttribute), @"title",
        textAttribute(element, kAXSubroleAttribute), @"subrole",
        textAttribute(element, CFSTR("AXIdentifier")), @"identifier",
        textAttribute(element, kAXDescriptionAttribute), @"description", nil];
    CFStringRef attributes[] = {kAXEnabledAttribute, kAXFocusedAttribute, kAXValueAttribute};
    NSString *keys[] = {@"enabled", @"focused", @"value"};
    BOOL secure = [textAttribute(element, kAXSubroleAttribute) isEqualToString:@"AXSecureTextField"];
    if (!secure) {
        NSString *selection = textAttribute(element, kAXSelectedTextAttribute);
        if ([selection length] && [selection length] <= 512) [node setObject:selection forKey:@"selectedText"];
    }
    for (int i = 0; i < 3; i++) {
        if (i == 2 && secure) continue;
        id value = attribute(element, attributes[i]);
        if ([value isKindOfClass:[NSString class]]) {
            if ([value length] > 512) value = [[value substringToIndex:512] stringByAppendingString:@"…"];
            [node setObject:value forKey:keys[i]];
        } else if ([value isKindOfClass:[NSNumber class]]) [node setObject:value forKey:keys[i]];
    }
    AXValueRef position = (AXValueRef)attribute(element, kAXPositionAttribute);
    AXValueRef size = (AXValueRef)attribute(element, kAXSizeAttribute);
    CGPoint p; CGSize s;
    if (position && size && CFGetTypeID(position) == AXValueGetTypeID() &&
        CFGetTypeID(size) == AXValueGetTypeID() &&
        AXValueGetValue(position, kAXValueCGPointType, &p) &&
        AXValueGetValue(size, kAXValueCGSizeType, &s)) {
        [node setObject:[NSArray arrayWithObjects:
            [NSNumber numberWithDouble:p.x], [NSNumber numberWithDouble:p.y],
            [NSNumber numberWithDouble:s.width], [NSNumber numberWithDouble:s.height], nil] forKey:@"frame"];
    }
    CFArrayRef actions = NULL;
    if (AXUIElementCopyActionNames(element, &actions) == kAXErrorSuccess) {
        [node setObject:(id)actions forKey:@"actions"];
        CFRelease(actions);
    }
    return node;
}

static void walk(AXUIElementRef element, NSString *path, int depth, int maxDepth,
                 NSUInteger limit, NSMutableArray *nodes, BOOL *truncated) {
    if ([nodes count] >= limit) { *truncated = YES; return; }
    [nodes addObject:describe(element, path)];
    NSArray *items = children(element);
    if (depth >= maxDepth) { if ([items count]) *truncated = YES; return; }
    for (NSUInteger i = 0; i < [items count]; i++) {
        if ([nodes count] >= limit) { *truncated = YES; break; }
        walk((AXUIElementRef)[items objectAtIndex:i],
             [path stringByAppendingFormat:@".%lu", (unsigned long)i],
             depth + 1, maxDepth, limit, nodes, truncated);
    }
}

static long integer(const char *value, long minimum, long maximum) {
    char *end = NULL;
    long number = strtol(value, &end, 10);
    if (!*value || *end || number < minimum || number > maximum) fail(@"Invalid integer argument");
    return number;
}

static AXUIElementRef application(const char *argument) {
    if (!AXAPIEnabled()) fail(@"Accessibility disabled: enable access for assistive devices in Universal Access");
    pid_t pid = (pid_t)integer(argument, 1, INT_MAX);
    if (![NSRunningApplication runningApplicationWithProcessIdentifier:pid]) fail(@"Application is not running");
    AXUIElementRef element = AXUIElementCreateApplication(pid);
    AXUIElementSetMessagingTimeout(element, 2.0);
    // Check the root explicitly: an AX error must not masquerade as an empty tree.
    CFTypeRef role = NULL;
    AXError error = AXUIElementCopyAttributeValue(element, kAXRoleAttribute, &role);
    if (role) CFRelease(role);
    if (error != kAXErrorSuccess) fail([NSString stringWithFormat:@"AX root read failed: %d", error]);
    return element;
}

static AXUIElementRef resolve(AXUIElementRef root, NSString *path) {
    NSArray *parts = [path componentsSeparatedByString:@"."];
    if (![[parts objectAtIndex:0] isEqualToString:@"0"]) fail(@"AX path must start with 0");
    AXUIElementRef current = root;
    CFRetain(current);
    for (NSUInteger i = 1; i < [parts count]; i++) {
        NSUInteger index = integer([[parts objectAtIndex:i] UTF8String], 0, 100000);
        NSArray *items = children(current);
        if (index >= [items count]) fail(@"AX path changed; take a new tree snapshot");
        AXUIElementRef next = (AXUIElementRef)[items objectAtIndex:index];
        CFRetain(next); CFRelease(current); current = next;
    }
    return current;
}

static void mouse(CGEventType kind, CGPoint point, CGMouseButton button) {
    CGEventRef event = CGEventCreateMouseEvent(NULL, kind, point, button);
    if (!event) fail(@"Cannot create mouse event");
    CGEventPost(kCGHIDEventTap, event); CFRelease(event);
    usleep(60000);
}

static CGPoint point(const char *x, const char *y) {
    return CGPointMake(integer(x, 0, CGDisplayPixelsWide(CGMainDisplayID()) - 1),
        integer(y, 0, CGDisplayPixelsHigh(CGMainDisplayID()) - 1));
}

static void wakeDisplay(void) {
    IOPMAssertionID assertion = kIOPMNullAssertionID;
    IOReturn result = IOPMAssertionDeclareUserActivity(CFSTR("OpenRevive GUI capture"), kIOPMUserActiveLocal, &assertion);
    if (result != kIOReturnSuccess) fail([NSString stringWithFormat:@"Display wake failed: %d", result]);
    // Lion can return a stale framebuffer while the backlight/display is idle.
    // Give WindowServer time to render after declaring user activity.
    usleep(1000000);
    if (assertion != kIOPMNullAssertionID) IOPMAssertionRelease(assertion);
}

int main(int argc, const char *argv[]) {
    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];
    if (argc < 2) fail(@"Commands: info | apps | windows | wake | capture PATH | tree PID [DEPTH=6] [LIMIT=500] | action PID PATH ACTION ROLE TITLE BUNDLE_ID | set PID PATH VALUE ROLE TITLE BUNDLE_ID | click X Y [right] | double-click X Y | move X Y | drag X1 Y1 X2 Y2 | scroll VERTICAL HORIZONTAL | type TEXT | key CODE [cmd,shift,alt,ctrl] | focus PID");
    NSString *command = [NSString stringWithUTF8String:argv[1]];
    if ([command isEqualToString:@"info"] && argc == 2) {
        NSRunningApplication *front = [[NSWorkspace sharedWorkspace] frontmostApplication];
        NSDictionary *session = [(id)CGSessionCopyCurrentDictionary() autorelease];
        CGEventRef event = CGEventCreate(NULL);
        CGPoint cursor = event ? CGEventGetLocation(event) : CGPointZero;
        if (event) CFRelease(event);
        output([NSDictionary dictionaryWithObjectsAndKeys:
            @"0.5.0", @"version",
            [NSArray arrayWithObjects:[NSNumber numberWithDouble:cursor.x], [NSNumber numberWithDouble:cursor.y], nil], @"cursor",
            [NSNumber numberWithBool:AXAPIEnabled()], @"accessibility",
            [NSNumber numberWithUnsignedLong:CGDisplayPixelsWide(CGMainDisplayID())], @"width",
            [NSNumber numberWithUnsignedLong:CGDisplayPixelsHigh(CGMainDisplayID())], @"height",
            [NSNumber numberWithInt:[front processIdentifier]], @"frontmostPid",
            [front localizedName] ?: @"", @"frontmostApp", session ?: [NSDictionary dictionary], @"session", nil]);
    } else if ([command isEqualToString:@"apps"] && argc == 2) {
        NSMutableArray *apps = [NSMutableArray array];
        for (NSRunningApplication *app in [[NSWorkspace sharedWorkspace] runningApplications]) {
            [apps addObject:[NSDictionary dictionaryWithObjectsAndKeys:
                [NSNumber numberWithInt:[app processIdentifier]], @"pid", [app localizedName] ?: @"", @"name",
                [app bundleIdentifier] ?: @"", @"bundleId", [NSNumber numberWithBool:[app isActive]], @"active", nil]];
        }
        output(apps);
    } else if ([command isEqualToString:@"windows"] && argc == 2) {
        CFArrayRef windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID);
        if (!windows) fail(@"Cannot read window list");
        output((id)windows); CFRelease(windows);
    } else if ([command isEqualToString:@"wake"] && argc == 2) {
        wakeDisplay(); output([NSDictionary dictionaryWithObject:@"user activity declared" forKey:@"result"]);
    } else if ([command isEqualToString:@"capture"] && argc == 3) {
        wakeDisplay();
        CGImageRef image = CGDisplayCreateImage(CGMainDisplayID());
        if (!image) fail(@"Cannot capture display in this login session");
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]]];
        CGImageDestinationRef destination = CGImageDestinationCreateWithURL((CFURLRef)url, CFSTR("public.png"), 1, NULL);
        if (!destination) fail(@"Cannot open capture destination");
        CGImageDestinationAddImage(destination, image, NULL);
        BOOL saved = CGImageDestinationFinalize(destination);
        CFRelease(destination); CGImageRelease(image);
        if (!saved) fail(@"Cannot save screenshot");
        output([NSDictionary dictionaryWithObject:[url path] forKey:@"capture"]);
    } else if ([command isEqualToString:@"tree"] && argc >= 3 && argc <= 5) {
        AXUIElementRef root = application(argv[2]);
        int depth = argc >= 4 ? (int)integer(argv[3], 0, 20) : 6;
        NSUInteger limit = argc == 5 ? integer(argv[4], 1, 5000) : 500;
        NSMutableArray *nodes = [NSMutableArray array]; BOOL truncated = NO;
        walk(root, @"0", 0, depth, limit, nodes, &truncated);
        output([NSDictionary dictionaryWithObjectsAndKeys:[NSNumber numberWithInt:atoi(argv[2])], @"pid",
            [[NSRunningApplication runningApplicationWithProcessIdentifier:atoi(argv[2])] bundleIdentifier] ?: @"", @"bundleId",
            [NSNumber numberWithDouble:[[NSDate date] timeIntervalSince1970]], @"capturedAt",
            nodes, @"nodes", [NSNumber numberWithBool:truncated], @"truncated", nil]);
        CFRelease(root);
    } else if (([command isEqualToString:@"action"] || [command isEqualToString:@"set"]) && argc == 8) {
        AXUIElementRef root = application(argv[2]);
        AXUIElementRef element = resolve(root, [NSString stringWithUTF8String:argv[3]]);
        if (![textAttribute(element, kAXRoleAttribute) isEqualToString:[NSString stringWithUTF8String:argv[5]]] ||
            ![textAttribute(element, kAXTitleAttribute) isEqualToString:[NSString stringWithUTF8String:argv[6]]])
            fail(@"Element role/title changed; take a new tree snapshot");
        // argv[7] is the expected owning application bundle id, to guard against PID reuse.
        NSRunningApplication *owner = [NSRunningApplication runningApplicationWithProcessIdentifier:atoi(argv[2])];
        if (![[owner bundleIdentifier] isEqualToString:[NSString stringWithUTF8String:argv[7]]]) fail(@"Owning application changed");
        CFStringRef value = (CFStringRef)[NSString stringWithUTF8String:argv[4]];
        AXError error = [command isEqualToString:@"set"] ? AXUIElementSetAttributeValue(element, kAXValueAttribute, value) : AXUIElementPerformAction(element, value);
        if (error != kAXErrorSuccess) fail([NSString stringWithFormat:@"AX operation failed: %d", error]);
        output([NSDictionary dictionaryWithObject:@"ok" forKey:@"result"]);
        CFRelease(element); CFRelease(root);
    } else if ([command isEqualToString:@"click"] && (argc == 4 || argc == 5)) {
        double x = integer(argv[2], 0, CGDisplayPixelsWide(CGMainDisplayID()) - 1);
        double y = integer(argv[3], 0, CGDisplayPixelsHigh(CGMainDisplayID()) - 1);
        BOOL right = argc == 5 && !strcmp(argv[4], "right");
        if (argc == 5 && !right) fail(@"Optional click button must be right");
        CGPoint point = CGPointMake(x, y);
        mouse(kCGEventMouseMoved, point, kCGMouseButtonLeft);
        mouse(right ? kCGEventRightMouseDown : kCGEventLeftMouseDown, point, right ? kCGMouseButtonRight : kCGMouseButtonLeft);
        mouse(right ? kCGEventRightMouseUp : kCGEventLeftMouseUp, point, right ? kCGMouseButtonRight : kCGMouseButtonLeft);
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"move"] && argc == 4) {
        mouse(kCGEventMouseMoved, point(argv[2], argv[3]), kCGMouseButtonLeft);
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"double-click"] && argc == 4) {
        CGPoint position = point(argv[2], argv[3]);
        mouse(kCGEventMouseMoved, position, kCGMouseButtonLeft);
        for (int count = 1; count <= 2; count++) {
            CGEventType kinds[] = {kCGEventLeftMouseDown, kCGEventLeftMouseUp};
            for (int i = 0; i < 2; i++) {
                CGEventRef event = CGEventCreateMouseEvent(NULL, kinds[i], position, kCGMouseButtonLeft);
                if (!event) fail(@"Cannot create mouse event");
                CGEventSetIntegerValueField(event, kCGMouseEventClickState, count);
                CGEventPost(kCGHIDEventTap, event); CFRelease(event); usleep(40000);
            }
        }
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"drag"] && argc == 6) {
        CGPoint start = point(argv[2], argv[3]), end = point(argv[4], argv[5]);
        mouse(kCGEventMouseMoved, start, kCGMouseButtonLeft);
        mouse(kCGEventLeftMouseDown, start, kCGMouseButtonLeft);
        for (int i = 1; i <= 20; i++) mouse(kCGEventLeftMouseDragged,
            CGPointMake(start.x + (end.x - start.x) * i / 20, start.y + (end.y - start.y) * i / 20), kCGMouseButtonLeft);
        mouse(kCGEventLeftMouseUp, end, kCGMouseButtonLeft);
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"scroll"] && argc == 4) {
        int vertical = integer(argv[2], -100, 100), horizontal = integer(argv[3], -100, 100);
        CGEventRef event = CGEventCreateScrollWheelEvent(NULL, kCGScrollEventUnitLine, 2, vertical, horizontal);
        if (!event) fail(@"Cannot create scroll event");
        CGEventPost(kCGHIDEventTap, event); CFRelease(event); usleep(60000);
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"type"] && argc == 3) {
        NSString *text = [NSString stringWithUTF8String:argv[2]];
        // Send complete composed characters (including surrogate pairs) without touching the clipboard.
        for (NSUInteger i = 0; i < [text length];) {
            NSRange range = [text rangeOfComposedCharacterSequenceAtIndex:i];
            UniChar *characters = malloc(sizeof(UniChar) * range.length);
            [text getCharacters:characters range:range];
            for (int down = 1; down >= 0; down--) {
                CGEventRef event = CGEventCreateKeyboardEvent(NULL, 0, down);
                CGEventKeyboardSetUnicodeString(event, range.length, characters);
                CGEventSetFlags(event, 0);
                CGEventPost(kCGSessionEventTap, event); CFRelease(event);
            }
            free(characters); i = NSMaxRange(range); usleep(15000);
        }
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"key"] && (argc == 3 || argc == 4)) {
        CGKeyCode code = integer(argv[2], 0, 127); CGEventFlags flags = 0;
        if (argc == 4) for (NSString *modifier in [[NSString stringWithUTF8String:argv[3]] componentsSeparatedByString:@","]) {
            if ([modifier isEqualToString:@"cmd"]) flags |= kCGEventFlagMaskCommand;
            else if ([modifier isEqualToString:@"shift"]) flags |= kCGEventFlagMaskShift;
            else if ([modifier isEqualToString:@"alt"]) flags |= kCGEventFlagMaskAlternate;
            else if ([modifier isEqualToString:@"ctrl"]) flags |= kCGEventFlagMaskControl;
            else fail(@"Unknown key modifier");
        }
        CGKeyCode modifierCodes[] = {55, 56, 58, 59};
        CGEventFlags modifierFlags[] = {kCGEventFlagMaskCommand, kCGEventFlagMaskShift, kCGEventFlagMaskAlternate, kCGEventFlagMaskControl};
        CGEventFlags held = 0;
        for (int i = 0; i < 4; i++) if (flags & modifierFlags[i]) {
            held |= modifierFlags[i];
            CGEventRef event = CGEventCreateKeyboardEvent(NULL, modifierCodes[i], true);
            CGEventSetFlags(event, held); CGEventPost(kCGSessionEventTap, event); CFRelease(event); usleep(30000);
        }
        for (int down = 1; down >= 0; down--) {
            CGEventRef event = CGEventCreateKeyboardEvent(NULL, code, down);
            CGEventSetFlags(event, flags); CGEventPost(kCGSessionEventTap, event); CFRelease(event); usleep(60000);
        }
        for (int i = 3; i >= 0; i--) if (flags & modifierFlags[i]) {
            held &= ~modifierFlags[i];
            CGEventRef event = CGEventCreateKeyboardEvent(NULL, modifierCodes[i], false);
            CGEventSetFlags(event, held); CGEventPost(kCGSessionEventTap, event); CFRelease(event); usleep(30000);
        }
        output([NSDictionary dictionaryWithObject:@"posted; verify UI" forKey:@"result"]);
    } else if ([command isEqualToString:@"focus"] && argc == 3) {
        pid_t pid = integer(argv[2], 1, INT_MAX);
        NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        if (!app || ![app activateWithOptions:NSApplicationActivateIgnoringOtherApps]) fail(@"Cannot activate app");
        if (AXAPIEnabled()) {
            AXUIElementRef root = AXUIElementCreateApplication(pid);
            AXUIElementSetAttributeValue(root, kAXFrontmostAttribute, kCFBooleanTrue);
            CFRelease(root);
        }
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2];
        while ([[[NSWorkspace sharedWorkspace] frontmostApplication] processIdentifier] != pid && [deadline timeIntervalSinceNow] > 0)
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        if ([[[NSWorkspace sharedWorkspace] frontmostApplication] processIdentifier] != pid) fail(@"App did not become frontmost");
        output([NSDictionary dictionaryWithObject:@"frontmost" forKey:@"result"]);
    } else fail(@"Unknown command or invalid argument count");
    [pool drain]; return 0;
}
