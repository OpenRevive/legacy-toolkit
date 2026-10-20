/* Bounded TCP reachability evidence on the explicit legacy iPad. */
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/time.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    struct sockaddr_in a;memset(&a,0,sizeof(a));a.sin_len=sizeof(a);a.sin_family=AF_INET;
    char *end=NULL;long port=strtol(argv[2],&end,10);
    if(!end || *end || port<1 || port>65535 || inet_pton(AF_INET,argv[1],&a.sin_addr)!=1) return 2;
    a.sin_port=htons((unsigned short)port);
    int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0) return 3;
    if(fcntl(fd,F_SETFL,O_NONBLOCK)<0) {close(fd);return 3;}
    struct timeval start,finish;gettimeofday(&start,NULL);
    int status=connect(fd,(struct sockaddr *)&a,sizeof(a)),error=errno;
    if(status<0 && error==EINPROGRESS) {
        fd_set ready;FD_ZERO(&ready);FD_SET(fd,&ready);struct timeval limit={6,0};
        status=select(fd+1,NULL,&ready,NULL,&limit);
        if(status>0) {socklen_t n=sizeof(error);if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&n)<0) error=errno;}
        else error=status==0 ? ETIMEDOUT : errno;
    } else if(status==0) error=0;
    gettimeofday(&finish,NULL);close(fd);
    double elapsed=finish.tv_sec-start.tv_sec+(finish.tv_usec-start.tv_usec)/1e6;
    printf("{\"ipv4\":\"%s\",\"port\":%ld,\"connected\":%s,\"errno\":%d,\"elapsedSeconds\":%.6f}\n",argv[1],port,error==0?"true":"false",error,elapsed);
    return 0;
}
