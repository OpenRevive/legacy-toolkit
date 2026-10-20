// A disposable real CFUserNotification dialog, with an independent response receipt.
#include <fcntl.h>
#include <sys/stat.h>
static int ORAlertProbe(NSString *name, NSDictionary *request, char **argv) {
    NSString *nonce=[request objectForKey:@"nonce"];
    NSCharacterSet *invalid=[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet];
    if (![nonce isKindOfClass:[NSString class]] || [nonce length]!=32 || [nonce rangeOfCharacterFromSet:invalid].location!=NSNotFound) return 2;
    NSString *path=[@"/var/root/OpenRevive/system-probe-" stringByAppendingFormat:@"%@.json",nonce];
    if ([name isEqual:@"probe-alert"]) {
        char *childArgs[]={argv[0],"probe-wait",argv[2],NULL};
        pid_t pid=fork();if (pid<0) return 2;
        if (pid==0) {setsid();int fd=open("/dev/null",O_RDWR);if (fd>=0) {dup2(fd,0);dup2(fd,1);dup2(fd,2);if (fd>2) close(fd);}execv(argv[0],childArgs);_exit(127);}
        NSData *result=[NSJSONSerialization dataWithJSONObject:[NSDictionary dictionaryWithObjectsAndKeys:@YES,@"ok",nonce,@"nonce",path,@"receiptPath",@(pid),@"probePid",nil] options:0 error:NULL];
        fwrite([result bytes],1,[result length],stdout);fputc('\n',stdout);return 0;
    }
    typedef SInt32 (*DisplayAlert)(CFTimeInterval,CFOptionFlags,CFURLRef,CFURLRef,CFURLRef,CFStringRef,CFStringRef,CFStringRef,CFStringRef,CFStringRef,CFOptionFlags *);
    DisplayAlert display=dlsym(RTLD_DEFAULT,"CFUserNotificationDisplayAlert");
    CFOptionFlags response=3;SInt32 code=-1;
    if (display) code=display(90,3,NULL,NULL,NULL,CFSTR("OpenRevive system probe"),(CFStringRef)nonce,CFSTR("Accept probe"),CFSTR("Cancel probe"),NULL,&response);
    umask(077);
    NSDictionary *receipt=[NSDictionary dictionaryWithObjectsAndKeys:nonce,@"nonce",@(code),@"error",@(response),@"responseFlags",@(code==0 && (response&3)==0),@"accepted",nil];
    NSData *data=[NSJSONSerialization dataWithJSONObject:receipt options:0 error:NULL];
    return [data writeToFile:path atomically:YES] ? 0 : 2;
}
