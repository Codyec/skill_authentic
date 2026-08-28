#include "QCamerav4l2.h"

//#include <opencv2/core/core.hpp>
//using namespace cv;


using namespace std;

//void sysfail(char *msg)
//{
//    perror(msg);
//}





//sysfail(#op);
#define vidioc(fd,op, arg) \
    if (ioctl(fd, VIDIOC_##op, arg) == -1)\
    { \
        return -1;\
    }\



list<int> g_fdList;
int CameraInitV4l2(const char *devName, int *fd)
{
    *fd = open(devName, O_RDWR);
    if (*fd == -1)
    {
        return -1;
    }
    g_fdList.push_back(*fd);

    return 0;
}

int CameraSetPersion(int fd, int width, int height, int type, int *size)
{

    struct v4l2_format v;

    int frame_bytes = 0;
    int FRAME_FORMAT = -1;
    if(type == 1)
    {
        frame_bytes = width * height ;
        FRAME_FORMAT = V4L2_PIX_FMT_GREY;
    }
    else
    {
        frame_bytes = width * height *3;
        FRAME_FORMAT = V4L2_PIX_FMT_RGB24;
    }

    v.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
    vidioc(fd,G_FMT, &v);
    v.fmt.pix.width = width;
    v.fmt.pix.height = height;
    v.fmt.pix.pixelformat = FRAME_FORMAT;
    v.fmt.pix.sizeimage = frame_bytes;
    vidioc(fd,S_FMT, &v);
    *size = frame_bytes;


    return 0;
}


unsigned char clip_value(unsigned char x,unsigned char min_val,unsigned char  max_val)
{
    if(x>max_val){
        return max_val;
    }else if(x<min_val){
        return min_val;
    }else{
        return x;
    }
}

bool RGB24_TO_YUV420(unsigned char *RgbBuf,int w,int h,unsigned char *yuvBuf)
{
    unsigned char*ptrY, *ptrU, *ptrV, *ptrRGB;
    memset(yuvBuf,0,w*h*3/2);
    ptrY = yuvBuf;
    ptrU = yuvBuf + w*h;
    ptrV = ptrU + (w*h*1/4);
    unsigned char y, u, v, r, g, b;
    for (int j = 0; j<h;j++){
        ptrRGB = RgbBuf + w*j*3 ;
        for (int i = 0;i<w;i++){

            r = *(ptrRGB++);
            g = *(ptrRGB++);
            b = *(ptrRGB++);
            y = (unsigned char)( ( 66 * r + 129 * g +  25 * b + 128) >> 8) + 16  ;
            u = (unsigned char)( ( -38 * r -  74 * g + 112 * b + 128) >> 8) + 128 ;
            v = (unsigned char)( ( 112 * r -  94 * g -  18 * b + 128) >> 8) + 128 ;
            *(ptrY++) = clip_value(y,0,255);
            if (j%2==0&&i%2 ==0){
                *(ptrU++) =clip_value(u,0,255);
            }
            else{
                if (i%2==0){
                *(ptrV++) =clip_value(v,0,255);
                }
            }
        }
    }
    return true;
}

int CameraWriteBuffer(int fd, char *buff, int size)
{
    if(0 >= ::write(fd, buff, size))
    {
        return -1;
    }
    return 0;
}

int CameraCloseV4l2(int fd)
{
    if(fd == -1) return 0;
    list<int>::iterator it=g_fdList.begin();
    bool flag = false;
    for(; it != g_fdList.end();it++)
    {
        if(fd == *it)
        {
            flag = true;
            g_fdList.erase(it);
            break;
        }
    }
    if(!flag) return -1;

    return close(fd);
}

int CameraCloseV4l2All()
{
    int fd = -1;

    list<int>::iterator it=g_fdList.begin();

    for(; it != g_fdList.end();it++)
    {
        fd = *it;
        if(fd == -1) continue;
        close(fd);
    }
    g_fdList.clear();
    g_fdList = list<int>();
    return 0;
}
