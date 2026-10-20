#import <UIKit/UIKit.h>
#import <Security/Security.h>
#import <Security/SecureTransport.h>
#import <CFNetwork/CFNetwork.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

static void Need(BOOL ok, NSString *why) {
    if (!ok) [NSException raise:@"OpenReviveNetworkProbe" format:@"%@", why];
}
static unsigned short U16(const unsigned char *p) { return (p[0] << 8) | p[1]; }
static NSUInteger Skip(const unsigned char *p, NSUInteger n, NSUInteger o) {
    while (o < n && p[o]) {
        if ((p[o] & 0xc0) == 0xc0) { Need(o+1<n,@"DNS pointer truncated"); return o+2; }
        Need(p[o]<=63 && o+p[o]+1<n,@"Invalid DNS label"); o += p[o]+1;
    }
    Need(o<n,@"DNS name truncated"); return o+1;
}
static int Connect(NSString *ip, int port, int kind) {
    int fd=socket(AF_INET,kind,0); Need(fd>=0,@"socket failed");
    struct sockaddr_in address; memset(&address,0,sizeof(address)); address.sin_len=sizeof(address);
    address.sin_family=AF_INET; address.sin_port=htons(port);
    if (inet_pton(AF_INET,[ip UTF8String],&address.sin_addr)!=1) { close(fd); Need(NO,@"Invalid IPv4"); }
    int flags=fcntl(fd,F_GETFL,0); fcntl(fd,F_SETFL,flags|O_NONBLOCK);
    int status=connect(fd,(struct sockaddr *)&address,sizeof(address));
    if(status<0 && errno==EINPROGRESS) {
        fd_set fds; FD_ZERO(&fds); FD_SET(fd,&fds); struct timeval wait={6,0};
        status=select(fd+1,NULL,&fds,NULL,&wait);
        int error=0; socklen_t size=sizeof(error);
        if(status>0 && getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size)==0 && error==0) status=0;
        else status=-1;
    }
    if(status<0) { close(fd); Need(NO,[NSString stringWithFormat:@"connect %@:%d failed",ip,port]); }
    fcntl(fd,F_SETFL,flags);
    struct timeval timeout={4,0}; setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    int yes=1; setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,sizeof(yes)); return fd;
}
static void ReadExact(int fd,void *data,size_t n) {
    size_t offset=0;
    while(offset<n) { ssize_t got=recv(fd,(char *)data+offset,n-offset,0); Need(got>0,@"DNS TCP read failed"); offset+=got; }
}
static NSString *Resolve(NSString *name,NSString *dns,BOOL tcp) {
    unsigned char query[512]={0}, response[4096]; unsigned short tid=arc4random()&65535;
    query[0]=tid>>8; query[1]=tid&255; query[2]=1; query[5]=1; NSUInteger length=12;
    for(NSString *part in [name componentsSeparatedByString:@"."]) {
        NSData *label=[part dataUsingEncoding:NSASCIIStringEncoding];
        Need(label.length>0 && label.length<=63 && length+label.length+6<512,@"Invalid DNS question");
        query[length++]=label.length; memcpy(query+length,label.bytes,label.length); length+=label.length;
    }
    query[length++]=0; query[length++]=0; query[length++]=1; query[length++]=0; query[length++]=1;
    int fd=Connect(dns,53,tcp?SOCK_STREAM:SOCK_DGRAM); ssize_t count=0;
    @try {
        if(tcp) {
            unsigned short framed=htons(length);
            Need(send(fd,&framed,2,0)==2 && send(fd,query,length,0)==(ssize_t)length,@"DNS TCP send failed");
            ReadExact(fd,&framed,2); count=ntohs(framed); Need(count<=4096 && count>=12,@"Invalid DNS TCP size");
            ReadExact(fd,response,count);
        } else {
            Need(send(fd,query,length,0)==(ssize_t)length,@"DNS UDP send failed"); count=recv(fd,response,sizeof(response),0);
        }
    } @finally { close(fd); }
    Need(count>=12 && U16(response)==tid && (response[2]&0x80) && !(response[2]&2) && (response[3]&15)==0,@"DNS response rejected");
    NSUInteger offset=12, sizeOfResponse=(NSUInteger)count;
    for(int i=0;i<U16(response+4);i++) { offset=Skip(response,sizeOfResponse,offset)+4; Need(offset<=sizeOfResponse,@"DNS question truncated"); }
    NSString *answer=nil;
    for(int i=0;i<U16(response+6);i++) {
        offset=Skip(response,sizeOfResponse,offset); Need(offset+10<=sizeOfResponse,@"DNS answer truncated");
        int type=U16(response+offset), klass=U16(response+offset+2), size=U16(response+offset+8); offset+=10;
        Need(offset+size<=sizeOfResponse,@"DNS data truncated");
        if(type==1 && klass==1 && size==4) { char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET,response+offset,ip,sizeof(ip)); answer=[NSString stringWithUTF8String:ip]; }
        offset+=size;
    }
    Need(answer!=nil,@"DNS did not return an A record"); return answer;
}
static OSStatus TLSRead(SSLConnectionRef connection,void *data,size_t *length) {
    size_t wanted=*length; ssize_t got=recv((int)(intptr_t)connection,data,wanted,0);
    *length=got>0?got:0;
    if(got==0) return errSSLClosedGraceful;
    if(got<0) return errno==EAGAIN || errno==EINTR ? errSSLWouldBlock : errSSLClosedAbort;
    return (size_t)got==wanted?noErr:errSSLWouldBlock;
}
static OSStatus TLSWrite(SSLConnectionRef connection,const void *data,size_t *length) {
    size_t wanted=*length; ssize_t sent=send((int)(intptr_t)connection,data,wanted,0);
    *length=sent>0?sent:0;
    if(sent<0) return errno==EAGAIN || errno==EINTR ? errSSLWouldBlock : errSSLClosedAbort;
    return (size_t)sent==wanted?noErr:errSSLWouldBlock;
}
static NSDictionary *Fetch(NSDictionary *site,NSString *dns,NSString *path,BOOL secure,NSString *anchor,NSString *policyName) {
    NSString *name=[site objectForKey:@"domain"], *udp=Resolve(name,dns,NO), *tcp=Resolve(name,dns,YES);
    Need([udp isEqual:tcp] && [udp isEqual:[site objectForKey:@"ipv4"]],@"Unexpected DNS address");
    int fd=Connect(udp,secure?443:80,SOCK_STREAM); SSLContextRef tls=NULL; SecTrustResultType trustResult=kSecTrustResultInvalid;
    BOOL trusted=NO; SSLProtocol protocol=kSSLProtocolUnknown; NSMutableData *received=[NSMutableData data];
    @try {
        if(secure) {
            tls=SSLCreateContext(NULL,kSSLClientSide,kSSLStreamType); Need(tls!=NULL,@"SSLCreateContext failed");
            Need(SSLSetIOFuncs(tls,TLSRead,TLSWrite)==noErr && SSLSetConnection(tls,(SSLConnectionRef)(intptr_t)fd)==noErr,@"TLS IO setup failed");
            Need(SSLSetPeerDomainName(tls,[name UTF8String],strlen([name UTF8String]))==noErr,@"TLS SNI setup failed");
            Need(SSLSetProtocolVersionMin(tls,kTLSProtocol12)==noErr,@"TLS 1.2 unavailable");
            Need(SSLSetSessionOption(tls,kSSLSessionOptionBreakOnServerAuth,true)==noErr,@"TLS trust callback unavailable");
            NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:20]; OSStatus status;
            do {
                status=SSLHandshake(tls);
                if(status==errSSLServerAuthCompleted) {
                    SecTrustRef peer=NULL; Need(SSLCopyPeerTrust(tls,&peer)==noErr && peer!=NULL,@"Peer trust unavailable");
                    NSMutableArray *chain=[NSMutableArray array];
                    for(CFIndex i=0;i<SecTrustGetCertificateCount(peer);i++) [chain addObject:(__bridge id)SecTrustGetCertificateAtIndex(peer,i)];
                    SecPolicyRef policy=SecPolicyCreateSSL(true,(__bridge CFStringRef)(policyName?:name));
                    SecTrustRef trust=NULL; OSStatus setup=SecTrustCreateWithCertificates((__bridge CFArrayRef)chain,policy,&trust);
                    CFRelease(policy); CFRelease(peer); Need(setup==noErr && trust!=NULL,@"Trust policy setup failed");
                    NSMutableArray *anchors=[NSMutableArray array];
                    if(anchor) {
                        NSData *der=[NSData dataWithContentsOfFile:[[NSBundle mainBundle] pathForResource:anchor ofType:@"cer"]];
                        SecCertificateRef root=der?SecCertificateCreateWithData(NULL,(__bridge CFDataRef)der):NULL;
                        Need(root!=NULL,@"Missing explicit root CA"); [anchors addObject:(__bridge id)root]; CFRelease(root);
                    }
                    setup|=SecTrustSetAnchorCertificates(trust,(__bridge CFArrayRef)anchors);
                    setup|=SecTrustSetAnchorCertificatesOnly(trust,true);
                    OSStatus evaluated=SecTrustEvaluate(trust,&trustResult); CFRelease(trust);
                    trusted=setup==noErr && evaluated==noErr && (trustResult==kSecTrustResultProceed || trustResult==kSecTrustResultUnspecified);
                    Need(trusted,[NSString stringWithFormat:@"certificate trust rejected: status=%d result=%u",(int)evaluated,(unsigned)trustResult]);
                } else Need(status==noErr || status==errSSLWouldBlock,[NSString stringWithFormat:@"TLS handshake failed: %d",(int)status]);
                Need([deadline timeIntervalSinceNow]>0,@"TLS handshake timeout");
            } while(status!=noErr);
            Need(trusted,@"TLS completed without a verified trust decision"); SSLGetNegotiatedProtocolVersion(tls,&protocol);
        }
        NSData *request=[[NSString stringWithFormat:@"GET %@ HTTP/1.0\r\nHost: %@\r\nConnection: close\r\nAccept-Encoding: identity\r\nUser-Agent: OpenRevive-iOS5-6-DNSProbe\r\n\r\n",path,name] dataUsingEncoding:NSASCIIStringEncoding];
        size_t offset=0; NSDate *deadline=[NSDate dateWithTimeIntervalSinceNow:20];
        while(offset<request.length) {
            size_t written=0; OSStatus status=noErr;
            if(tls) status=SSLWrite(tls,(const char *)request.bytes+offset,request.length-offset,&written);
            else { ssize_t n=send(fd,(const char *)request.bytes+offset,request.length-offset,0); Need(n>0,@"HTTP write failed"); written=n; }
            offset+=written; Need(status==noErr || status==errSSLWouldBlock,@"TLS write failed"); Need([deadline timeIntervalSinceNow]>0,@"HTTP write timeout");
        }
        for(;;) {
            char buffer[8192]; size_t count=0; OSStatus status=noErr;
            if(tls) status=SSLRead(tls,buffer,sizeof(buffer),&count);
            else { ssize_t n=recv(fd,buffer,sizeof(buffer),0); Need(n>=0,@"HTTP read failed"); count=n; if(n==0) status=errSSLClosedGraceful; }
            if(count) [received appendBytes:buffer length:count];
            Need(received.length<1024*1024,@"HTTP response too large");
            if(status==errSSLClosedGraceful || status==errSSLClosedNoNotify) break;
            Need(status==noErr || status==errSSLWouldBlock,[NSString stringWithFormat:@"TLS read failed: %d",(int)status]);
            Need([deadline timeIntervalSinceNow]>0,@"HTTP read timeout");
        }
    } @finally { if(tls) CFRelease(tls); close(fd); }
    CFHTTPMessageRef message=CFHTTPMessageCreateEmpty(NULL,false);
    BOOL parsed=CFHTTPMessageAppendBytes(message,received.bytes,received.length) && CFHTTPMessageIsHeaderComplete(message);
    int status=CFHTTPMessageGetResponseStatusCode(message); NSData *body=CFBridgingRelease(CFHTTPMessageCopyBody(message)); CFRelease(message);
    Need(parsed && status==200,[NSString stringWithFormat:@"HTTP response rejected: %d",status]);
    return @{@"domain":name,@"dns":dns,@"dnsUDP":udp,@"dnsTCP":tcp,@"status":@(status),@"trusted":@(trusted),
             @"tls":secure?(protocol==kTLSProtocol12?@"TLSv1.2":[NSString stringWithFormat:@"%u",(unsigned)protocol]):@"none",
             @"trustResult":@(trustResult),@"body":body?:[NSData data]};
}

@interface Probe : UIResponder <UIApplicationDelegate, UIWebViewDelegate>
@property(nonatomic,strong) UIWindow *window;
@property(nonatomic,strong) UILabel *label;
@property(nonatomic,strong) UIWebView *web;
@property(nonatomic,strong) NSMutableDictionary *receipt;
@property(nonatomic,strong) NSMutableArray *pages;
@property(nonatomic) NSUInteger current;
@end
@implementation Probe
- (void)save {
    NSString *dir=[NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES) objectAtIndex:0];
    NSData *json=[NSJSONSerialization dataWithJSONObject:self.receipt options:NSJSONWritingPrettyPrinted error:NULL];
    [json writeToFile:[dir stringByAppendingPathComponent:@"dns-ca-result.json"] atomically:YES];
}
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
    app.idleTimerDisabled=YES;
    self.window=[[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds]; UIViewController *controller=[UIViewController new];
    self.window.rootViewController=controller; controller.view.backgroundColor=[UIColor colorWithRed:.06 green:.14 blue:.13 alpha:1];
    self.label=[[UILabel alloc] initWithFrame:CGRectMake(12,20,CGRectGetWidth(self.window.bounds)-24,80)]; self.label.numberOfLines=4; self.label.font=[UIFont systemFontOfSize:13];
    self.label.textColor=[UIColor whiteColor]; self.label.backgroundColor=[UIColor clearColor]; self.label.text=@"OpenRevive DNS + CA\nChecking network…";
    [controller.view addSubview:self.label];
    self.web=[[UIWebView alloc] initWithFrame:CGRectMake(0,106,CGRectGetWidth(self.window.bounds),CGRectGetHeight(self.window.bounds)-126)]; self.web.autoresizingMask=UIViewAutoresizingFlexibleHeight|UIViewAutoresizingFlexibleWidth;
    self.web.delegate=self; [controller.view addSubview:self.web]; [self.window makeKeyAndVisible];
    [self performSelectorInBackground:@selector(run) withObject:nil]; return YES;
}
- (void)run { @autoreleasepool {
    NSDictionary *config=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[[NSBundle mainBundle] pathForResource:@"probe-config" ofType:@"json"]] options:0 error:NULL];
    self.receipt=[@{@"run":[config objectForKey:@"run"]?:@"missing",@"passed":@NO,@"tests":[NSMutableArray array],@"rendered":[NSMutableArray array],@"platform":[UIDevice currentDevice].model,@"ios":[UIDevice currentDevice].systemVersion} mutableCopy];
    self.pages=[NSMutableArray array];
    @try {
        for(NSDictionary *site in [config objectForKey:@"sites"]) {
            NSString *path=[NSString stringWithFormat:@"/health?probe=%@",[config objectForKey:@"run"]];
            NSMutableDictionary *test=[NSMutableDictionary dictionary];
            for(NSNumber *secure in @[@NO,@YES]) {
                NSMutableDictionary *result=[Fetch(site,[config objectForKey:@"dns"],path,secure.boolValue,[site objectForKey:@"anchor"],nil) mutableCopy];
                NSDictionary *health=[NSJSONSerialization JSONObjectWithData:[result objectForKey:@"body"] options:0 error:NULL];
                Need([[health objectForKey:@"service"] isEqual:[site objectForKey:@"service"]] && [[health objectForKey:@"deployment_id"] isEqual:[site objectForKey:@"deployment_id"]],@"Wrong origin health marker");
                [result removeObjectForKey:@"body"]; [result setObject:health forKey:@"health"]; [test setObject:result forKey:secure.boolValue?@"https":@"http"];
            }
            for(NSString *negative in @[@"unknownCA",@"wrongName"]) {
                BOOL rejected=NO;
                @try { Fetch(site,[config objectForKey:@"dns"],@"/health",YES,[negative isEqual:@"unknownCA"]?nil:[site objectForKey:@"anchor"],[negative isEqual:@"wrongName"]?@"wrong.openrevive.test":nil); }
                @catch(NSException *e) { Need([e.reason hasPrefix:@"certificate trust rejected:"],e.reason); rejected=YES; [test setObject:e.reason forKey:negative]; }
                Need(rejected,[NSString stringWithFormat:@"TLS incorrectly accepted %@",negative]);
            }
            NSDictionary *page=Fetch(site,[config objectForKey:@"dns"],@"/",YES,[site objectForKey:@"anchor"],nil);
            NSString *html=[[NSString alloc] initWithData:[page objectForKey:@"body"] encoding:NSUTF8StringEncoding];
            Need(html!=nil && [html rangeOfString:[site objectForKey:@"deployment_id"]].location!=NSNotFound,@"Wrong HTML origin marker");
            [self.pages addObject:@{@"site":site,@"html":html}]; [[self.receipt objectForKey:@"tests"] addObject:test];
        }
        [self performSelectorOnMainThread:@selector(renderNext) withObject:nil waitUntilDone:NO];
    } @catch(NSException *e) {
        [self.receipt setObject:e.reason?:@"Unknown exception" forKey:@"error"]; [self save];
        [self performSelectorOnMainThread:@selector(showFailure) withObject:nil waitUntilDone:NO];
    }
} }
- (void)showFailure { self.label.text=[NSString stringWithFormat:@"OpenRevive — FAILED\n%@",[self.receipt objectForKey:@"error"]]; }
- (void)renderNext {
    if(self.current>=self.pages.count) { [self.receipt setObject:@YES forKey:@"passed"]; [self save]; self.label.text=[NSString stringWithFormat:@"OpenRevive — PASS\n%lu origins · DNS UDP/TCP\nTLS 1.2 · explicit CA · negative checks",(unsigned long)self.pages.count]; return; }
    NSDictionary *page=[self.pages objectAtIndex:self.current],*site=[page objectForKey:@"site"];
    self.label.text=[NSString stringWithFormat:@"OpenRevive DNS + CA\n%@\n%@ → %@",[site objectForKey:@"domain"],[[[[self.receipt objectForKey:@"tests"] objectAtIndex:self.current] objectForKey:@"https"] objectForKey:@"dns"],[site objectForKey:@"ipv4"]];
    [self.web loadHTMLString:[page objectForKey:@"html"] baseURL:[NSURL URLWithString:[NSString stringWithFormat:@"https://%@/",[site objectForKey:@"domain"]]]];
}
- (void)webViewDidFinishLoad:(UIWebView *)webView {
    if(self.current>=self.pages.count) return;
    NSDictionary *site=[[self.pages objectAtIndex:self.current] objectForKey:@"site"];
    NSString *dom=[webView stringByEvaluatingJavaScriptFromString:@"document.documentElement.innerHTML"];
    if([dom rangeOfString:[site objectForKey:@"deployment_id"]].location==NSNotFound) { [self.receipt setObject:@"WebView DOM marker mismatch" forKey:@"error"]; [self save]; [self showFailure]; return; }
    [[self.receipt objectForKey:@"rendered"] addObject:[site objectForKey:@"domain"]]; self.current++; [self save];
    [self performSelector:@selector(renderNext) withObject:nil afterDelay:1.0];
}
@end
int main(int argc,char **argv) { @autoreleasepool { return UIApplicationMain(argc,argv,nil,NSStringFromClass([Probe class])); } }
