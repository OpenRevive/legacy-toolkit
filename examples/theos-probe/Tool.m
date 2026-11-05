#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#include <sys/sysctl.h>
#include <stdio.h>
#include <dlfcn.h>
#include <unistd.h>

static int printJSON(id value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];
    if (!data || fwrite([data bytes], 1, [data length], stdout) != [data length]) return 1;
    puts("");
    return 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc == 3 && strcmp(argv[1], "screenshot") == 0) {
            void *ui = dlopen("/System/Library/Frameworks/UIKit.framework/UIKit", RTLD_NOW);
            CGImageRef (*capture)(void) = dlsym(ui, "UIGetScreenImage");
            CGImageRef image = capture ? capture() : NULL;
            if (!image) { fputs("Native screenshot unavailable\n", stderr); return 2; }
            NSData *png = UIImagePNGRepresentation([UIImage imageWithCGImage:image]);
            BOOL saved = [png writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES];
            NSDictionary *result = [NSDictionary dictionaryWithObjectsAndKeys:
                [NSNumber numberWithBool:saved], @"saved",
                [NSNumber numberWithUnsignedLong:CGImageGetWidth(image)], @"width",
                [NSNumber numberWithUnsignedLong:CGImageGetHeight(image)], @"height", nil];
            CGImageRelease(image);
            return saved ? printJSON(result) : 2;
        }
        if (argc == 2 && strcmp(argv[1], "state") == 0) {
            void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", RTLD_NOW);
            CFStringRef (*front)(void) = dlsym(sbs, "SBSCopyFrontmostApplicationDisplayIdentifier");
            if (!front) return 2;
            CFStringRef identifier = front();
            NSDictionary *result = [NSDictionary dictionaryWithObject:(NSString *)(identifier ?: CFSTR("")) forKey:@"frontmost_bundle_id"];
            int code = printJSON(result);
            if (identifier) CFRelease(identifier);
            return code;
        }
        char machine[64] = {0}; size_t length = sizeof(machine);
        if (sysctlbyname("hw.machine", machine, &length, NULL, 0)) return 1;
        NSDictionary *receipt = [NSDictionary dictionaryWithObjectsAndKeys:
            @"OPENREVIVE_THEOS_DEVICE_OK", @"result", @"armv7", @"architecture",
            [NSString stringWithUTF8String:machine], @"hardware",
            [[NSProcessInfo processInfo] operatingSystemVersionString], @"os",
            argc > 1 ? [NSString stringWithUTF8String:argv[1]] : @"", @"nonce", nil];
        return printJSON(receipt);
    }
}
