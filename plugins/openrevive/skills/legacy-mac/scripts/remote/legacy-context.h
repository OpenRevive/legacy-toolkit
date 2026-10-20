// Shared local RPC; used outside SpringBoard, never recursively on its main thread.
static NSDictionary *ORPortRequestTimed(NSString *name, NSDictionary *request, NSString **error, CFTimeInterval seconds) {
    CFMessagePortRef port=CFMessagePortCreateRemote(NULL,(CFStringRef)name);
    if (!port) {if (error) *error=@"Bridge endpoint unavailable; run setup-uikit --respring";return nil;}
    NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:NULL];CFDataRef reply=NULL;
    SInt32 status=CFMessagePortSendRequest(port,1,(CFDataRef)data,MIN(2,seconds),seconds,kCFRunLoopDefaultMode,&reply);CFRelease(port);
    if (status || !reply) {if (error) *error=[NSString stringWithFormat:@"Bridge request failed: %ld",(long)status];return nil;}
    id result=[NSJSONSerialization JSONObjectWithData:(NSData *)reply options:0 error:NULL];CFRelease(reply);
    if (![result isKindOfClass:[NSDictionary class]]) {if (error) *error=@"Bridge returned invalid JSON";return nil;}
    return result;
}

static NSDictionary *ORPortRequest(NSString *name, NSDictionary *request, NSString **error) {return ORPortRequestTimed(name,request,error,10);}
