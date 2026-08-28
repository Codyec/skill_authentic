# cmake needs this line
cmake_minimum_required(VERSION 2.8)

# Enable C++11
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)

# Define project name
project(opencv_demo)

# Find OpenCV, you may need to set OpenCV_DIR variable
# to the absolute path to the directory containing OpenCVConfig.cmake file
# via the command line or GUI
find_package(OpenCV REQUIRED)  
include_directories( ${OpenCV_INCLUDE_DIRS} )  #新复制过来的是没有这一行的，自己加的
# Declare the executable target built from your sources
add_executable(opencv_demo opencv_demo.cpp)

# Link your application with OpenCV libraries
target_link_libraries(opencv_demo ${OpenCV_LIBS})
