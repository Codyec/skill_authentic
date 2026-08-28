#ifndef QCAMERAV4L2_H
#define QCAMERAV4L2_H

#include <list>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <iostream>
#include <linux/videodev2.h>


extern std::list<int> g_fdList;


unsigned char clip_value(unsigned char x,unsigned char min_val,unsigned char  max_val);
bool RGB24_TO_YUV420(unsigned char *RgbBuf,int w,int h,unsigned char *yuvBuf);
//void sysfail(char *msg);

/*
    初始化设备
    devName；设备名    传入
    fd：文件设备描述符  传出
    return：0成功，非0失败
*/
int  CameraInitV4l2(const char *devName,int *fd);

/*
    设置写入的图像属性
    fd：文件设备描述符
    width：写入图像的宽度
    height：写入图像的高度
    type：写入黑白或者彩色
    size：传出的写入大小
    return：0成功，非0失败

*/
int CameraSetPersion(int fd,int width,int height,int type,int *size);


/*
    写入图像
    fd：文件设备描述符
    buff：写入的图像数据
    size：写入的数据大小
    return：0成功，非0失败

*/
int CameraWriteBuffer(int fd, char *buff,int size);



/*
    关闭设备
    fd：文件设备描述符
    return：0成功，非0失败

*/
int CameraCloseV4l2(int fd);


/*
    关闭所有文件
    return：0成功，非0失败
*/

int CameraCloseV4l2All();




#endif // CAMERAV4L2_H
