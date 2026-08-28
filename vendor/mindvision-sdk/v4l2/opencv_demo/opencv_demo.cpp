#include <unistd.h>
#include<opencv2/opencv.hpp>
using namespace cv;

int main() {
    VideoCapture cap;

    bool flag = false;


    int index = 0;
    while(1)
    {

        if(!flag)
        {
            cap.open(0); //打开摄像头

            if (!cap.isOpened())
            {
                destroyAllWindows();
                sleep(1);
                continue;
            }

            flag = true;
            Mat frame;
            while (1)
            {
                cap >> frame;//等价于cap.read(frame);
                if (frame.empty())
                {
                    flag = false;
                    cap.release();
                    break;
                }

                //cvtColor(frame,frame, COLOR_BGR2GRAY);
                //frame.resize(400,400);
                imshow("video", frame);
                if (char(waitKey(1))=='q')//按下任意键退出摄像头　　因电脑环境而异，有的电脑可能会出现一闪而过的情况
                {
                    flag = false;
                    cap.release();
                    break;
                }
            }
        }
    }




    cap.release();
    destroyAllWindows();//关闭所有窗口
    return 0;
}
