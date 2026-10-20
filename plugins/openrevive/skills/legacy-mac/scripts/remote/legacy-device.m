// OpenRevive iOS 5/6 command helper. Built by the user's Xcode; no private SDK.
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv) {
    // lockdownd ignores SIGCHLD. Reset it before executing tools such as dpkg,
    // otherwise waitpid fails with ECHILD despite a successful child spawn.
    signal(SIGCHLD, SIG_DFL);
    signal(SIGPIPE, SIG_DFL);
    if (argc > 3 && strcmp(argv[1], "run") == 0) {
        int seconds = atoi(argv[2]);
        if (seconds < 1 || seconds > 300) return 2;
        pid_t child = fork();
        if (child < 0) { perror("fork"); return 2; }
        if (child == 0) { setpgid(0,0); execvp(argv[3],argv+3); perror("execvp"); _exit(127); }
        setpgid(child,child);
        time_t deadline = time(NULL) + seconds;
        int status = 0;
        while (1) {
            pid_t waited = waitpid(child,&status,WNOHANG);
            if (waited == child) break;
            if (waited < 0) {
                if (errno == EINTR) continue;
                perror("waitpid"); return 2;
            }
            if (time(NULL) >= deadline) {
                kill(-child,SIGTERM); usleep(100000); kill(-child,SIGKILL);
                waitpid(child,&status,0); return 124;
            }
            usleep(10000);
        }
        return WIFEXITED(status) ? WEXITSTATUS(status) : 128+WTERMSIG(status);
    }
    if (argc > 2 && strcmp(argv[1], "exec") == 0) {
        execvp(argv[2], argv + 2); perror("execvp"); return 127;
    }
    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];
    void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", RTLD_NOW);
    if (!sbs) { fprintf(stderr,"SpringBoardServices unavailable\n"); return 2; }
    NSDictionary *result = nil;
    int code = 0;
    if (argc == 3 && strcmp(argv[1], "launch") == 0) {
        int (*launch)(CFStringRef, Boolean) = dlsym(sbs, "SBSLaunchApplicationWithIdentifier");
        if (!launch) { fprintf(stderr,"Legacy launch API unavailable\n"); return 2; }
        NSString *bundle = [NSString stringWithUTF8String:argv[2]];
        code = launch((CFStringRef)bundle, false);
        result = [NSDictionary dictionaryWithObjectsAndKeys:bundle,@"bundleId",
            [NSNumber numberWithInt:code],@"launchStatus",nil];
    } else if (argc == 2 && strcmp(argv[1], "state") == 0) {
        CFStringRef (*front)(void) = dlsym(sbs,"SBSCopyFrontmostApplicationDisplayIdentifier");
        if (!front) { fprintf(stderr,"Frontmost application API unavailable\n"); return 2; }
        mach_port_t (*port)(void) = dlsym(sbs,"SBSSpringBoardServerPort");
        void (*locked)(mach_port_t, BOOL *, BOOL *) = dlsym(sbs,"SBGetScreenLockStatus");
        CFStringRef app = front ? front() : NULL;
        BOOL lockStatus = NO, passcode = NO;
        if (port && locked) locked(port(),&lockStatus,&passcode);
        result = [NSDictionary dictionaryWithObjectsAndKeys:
            [NSNumber numberWithInt:getuid()],@"uid",(NSString *)(app ?: CFSTR("")),@"frontmostBundleId",
            [NSNumber numberWithBool:lockStatus],@"screenLocked",
            [NSNumber numberWithBool:passcode],@"passcodeEnabled",
            [NSNumber numberWithBool:port && locked],@"lockStateAvailable",nil];
        if (app) CFRelease(app);
    } else { fprintf(stderr,"Usage: legacy-device state | launch BUNDLE | exec PROGRAM ARG... | run SECONDS PROGRAM ARG...\n"); return 2; }
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
    fwrite([json bytes],1,[json length],stdout); fputc('\n',stdout);
    [pool drain]; return code == 0 ? 0 : 1;
}
