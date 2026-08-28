#!/bin/bash
clear
A=`whoami`

if [ $A != 'root' ]; then
   echo "请使用管理员权限运行, sudo ./install.sh"
   echo "非管理员权限执行,已退出 !!!"
   exit 1;
fi
echo "现在开始编译V4l2."
sudo rmmod v4l2loopback
cd v4l2loopback
make
sudo make install
echo "编译完成"
sudo modprobe v4l2loopback video_nr=0,1,2,3,4,5,6,7
echo "加载V4l2完成"
echo "安装结束"
