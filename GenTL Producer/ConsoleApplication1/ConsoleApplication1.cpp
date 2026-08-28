#include <windows.h>
#include <iostream>
#include <fstream>
#include <vector>

#include "GenTL.h"

#pragma warning(disable:4996)

#define CHECK_HANDLE(x) \
if (!(x)) \
{ \
    std::cout << #x << " is null" << std::endl; \
    return -1; \
}

#define CHECK_ERR(err, msg) \
if ((err) != GC_ERR_SUCCESS) \
{ \
    std::cout << msg << " failed: " << err << std::endl; \
    return -1; \
}

using namespace GenTL;

//====================================================
// GenTL Function Pointer Types
//====================================================

typedef int32_t(__stdcall* PGCInitLib)(void);
typedef int32_t(__stdcall* PGCCloseLib)(void);

typedef int32_t(__stdcall* PTLOpen)(TL_HANDLE*);
typedef int32_t(__stdcall* PTLClose)(TL_HANDLE);

typedef int32_t(__stdcall* PTLUpdateInterfaceList)(
    TL_HANDLE,
    bool8_t*,
    uint64_t);

typedef int32_t(__stdcall* PTLGetNumInterfaces)(
    TL_HANDLE,
    uint32_t*);

typedef int32_t(__stdcall* PTLGetInterfaceID)(
    TL_HANDLE,
    uint32_t,
    char*,
    size_t*);

typedef int32_t(__stdcall* PTLOpenInterface)(
    TL_HANDLE,
    const char*,
    IF_HANDLE*);

typedef int32_t(__stdcall* PIFClose)(
    IF_HANDLE);

typedef int32_t(__stdcall* PIFUpdateDeviceList)(
    IF_HANDLE,
    bool8_t*,
    uint64_t);

typedef int32_t(__stdcall* PIFGetNumDevices)(
    IF_HANDLE,
    uint32_t*);

typedef int32_t(__stdcall* PIFGetDeviceID)(
    IF_HANDLE,
    uint32_t,
    char*,
    size_t*);

typedef int32_t(__stdcall* PIFGetDeviceInfo)(
    IF_HANDLE,
    const char*,
    DEVICE_INFO_CMD,
    INFO_DATATYPE*,
    void*,
    size_t*);

typedef int32_t(__stdcall* PIFOpenDevice)(
    IF_HANDLE,
    const char*,
    DEVICE_ACCESS_FLAGS,
    DEV_HANDLE*);

typedef int32_t(__stdcall* PDevClose)(
    DEV_HANDLE);

//====================================================

int main()
{
    //------------------------------------------------
    // 修改成你的 cti 路径
    //------------------------------------------------

    const char* ctiPath =
        "C:\\Program Files (x86)\\MindVision\\Demo\\VC++\\ConsoleApplication1\\WinMVGenTL_V1.0.5.cti";

    //------------------------------------------------
    // Load CTI
    //------------------------------------------------

    HMODULE hCTI = LoadLibraryA(ctiPath);

    CHECK_HANDLE(hCTI);

    std::cout << "LoadLibrary success" << std::endl;

    //------------------------------------------------
    // GetProcAddress
    //------------------------------------------------

    auto GCInitLib =
        (PGCInitLib)GetProcAddress(hCTI, "GCInitLib");

    auto GCCloseLib =
        (PGCCloseLib)GetProcAddress(hCTI, "GCCloseLib");

    auto TLOpen =
        (PTLOpen)GetProcAddress(hCTI, "TLOpen");

    auto TLClose =
        (PTLClose)GetProcAddress(hCTI, "TLClose");

    auto TLUpdateInterfaceList =
        (PTLUpdateInterfaceList)GetProcAddress(
            hCTI,
            "TLUpdateInterfaceList");

    auto TLGetNumInterfaces =
        (PTLGetNumInterfaces)GetProcAddress(
            hCTI,
            "TLGetNumInterfaces");

    auto TLGetInterfaceID =
        (PTLGetInterfaceID)GetProcAddress(
            hCTI,
            "TLGetInterfaceID");

    auto TLOpenInterface =
        (PTLOpenInterface)GetProcAddress(
            hCTI,
            "TLOpenInterface");

    auto IFClose =
        (PIFClose)GetProcAddress(
            hCTI,
            "IFClose");

    auto IFUpdateDeviceList =
        (PIFUpdateDeviceList)GetProcAddress(
            hCTI,
            "IFUpdateDeviceList");

    auto IFGetNumDevices =
        (PIFGetNumDevices)GetProcAddress(
            hCTI,
            "IFGetNumDevices");

    auto IFGetDeviceID =
        (PIFGetDeviceID)GetProcAddress(
            hCTI,
            "IFGetDeviceID");

    auto IFGetDeviceInfo =
        (PIFGetDeviceInfo)GetProcAddress(
            hCTI,
            "IFGetDeviceInfo");

    auto IFOpenDevice =
        (PIFOpenDevice)GetProcAddress(
            hCTI,
            "IFOpenDevice");

    auto DevClose =
        (PDevClose)GetProcAddress(
            hCTI,
            "DevClose");

    CHECK_HANDLE(GCInitLib);
    CHECK_HANDLE(TLOpen);
    CHECK_HANDLE(TLUpdateInterfaceList);

    //------------------------------------------------
    // Init
    //------------------------------------------------

    int32_t err;

    err = GCInitLib();

    CHECK_ERR(err, "GCInitLib");

    std::cout << "GCInitLib success" << std::endl;

    //------------------------------------------------
    // Open TL
    //------------------------------------------------

    TL_HANDLE hTL = nullptr;

    err = TLOpen(&hTL);

    CHECK_ERR(err, "TLOpen");

    std::cout << "TLOpen success" << std::endl;

    //------------------------------------------------
    // Update Interface List
    //------------------------------------------------

    bool8_t changed = false;

    std::cout << "Before TLUpdateInterfaceList"
        << std::endl;

    err = TLUpdateInterfaceList(
        hTL,
        &changed,
        1000);

    std::cout << "After TLUpdateInterfaceList"
        << std::endl;

    CHECK_ERR(err, "TLUpdateInterfaceList");

    //------------------------------------------------
    // Interface Count
    //------------------------------------------------

    uint32_t ifCount = 0;

    err = TLGetNumInterfaces(
        hTL,
        &ifCount);

    CHECK_ERR(err, "TLGetNumInterfaces");

    std::cout << "Interface Count: "
        << ifCount << std::endl;

    //------------------------------------------------
    // Enum Interfaces
    //------------------------------------------------

    for (uint32_t i = 0; i < ifCount; i++)
    {
        char ifID[1024] = { 0 };

        size_t size = sizeof(ifID);

        err = TLGetInterfaceID(
            hTL,
            i,
            ifID,
            &size);

        if (err != GC_ERR_SUCCESS)
        {
            std::cout << "TLGetInterfaceID failed"
                << std::endl;
            continue;
        }

        std::cout << "Interface ID: "
            << ifID << std::endl;

        //--------------------------------------------
        // Open Interface
        //--------------------------------------------

        IF_HANDLE hIF = nullptr;

        std::cout << "Before TLOpenInterface"
            << std::endl;

        err = TLOpenInterface(
            hTL,
            ifID,
            &hIF);

        std::cout << "After TLOpenInterface"
            << std::endl;

        if (err != GC_ERR_SUCCESS)
        {
            std::cout << "TLOpenInterface failed: "
                << err << std::endl;

            continue;
        }

        //--------------------------------------------
        // Update Device List
        //--------------------------------------------

        bool8_t devChanged = false;

        std::cout << "Before IFUpdateDeviceList"
            << std::endl;

        err = IFUpdateDeviceList(
            hIF,
            &devChanged,
            1000);

        std::cout << "After IFUpdateDeviceList"
            << std::endl;

        if (err != GC_ERR_SUCCESS)
        {
            std::cout << "IFUpdateDeviceList failed: "
                << err << std::endl;

            IFClose(hIF);

            continue;
        }

        //--------------------------------------------
        // Device Count
        //--------------------------------------------

        uint32_t devCount = 0;

        err = IFGetNumDevices(
            hIF,
            &devCount);

        if (err != GC_ERR_SUCCESS)
        {
            std::cout << "IFGetNumDevices failed"
                << std::endl;

            IFClose(hIF);

            continue;
        }

        std::cout << "Device Count: "
            << devCount << std::endl;

        //--------------------------------------------
        // Enum Device
        //--------------------------------------------

        for (uint32_t d = 0; d < devCount; d++)
        {
            char devID[1024] = { 0 };

            size_t devSize = sizeof(devID);

            err = IFGetDeviceID(
                hIF,
                d,
                devID,
                &devSize);

            if (err != GC_ERR_SUCCESS)
            {
                std::cout << "IFGetDeviceID failed"
                    << std::endl;

                continue;
            }

            std::cout << "Device ID: "
                << devID << std::endl;

            //----------------------------------------
            // Device Info
            //----------------------------------------

            char infoBuf[1024] = { 0 };

            size_t infoSize = sizeof(infoBuf);

            INFO_DATATYPE infoType;

            std::cout << "Before IFGetDeviceInfo"
                << std::endl;

            err = IFGetDeviceInfo(
                hIF,
                devID,
                DEVICE_INFO_MODEL,
                &infoType,
                infoBuf,
                &infoSize);

            std::cout << "After IFGetDeviceInfo"
                << std::endl;

            if (err == GC_ERR_SUCCESS)
            {
                std::cout << "Model: "
                    << infoBuf << std::endl;
            }
            else
            {
                std::cout << "IFGetDeviceInfo failed: "
                    << err << std::endl;
            }

            //----------------------------------------
            // Open Device
            //----------------------------------------

            DEV_HANDLE hDev = nullptr;

            err = IFOpenDevice(
                hIF,
                devID,
                DEVICE_ACCESS_CONTROL,
                &hDev);

            if (err != GC_ERR_SUCCESS)
            {
                std::cout << "IFOpenDevice failed: "
                    << err << std::endl;

                continue;
            }

            std::cout << "Device Open Success"
                << std::endl;

            DevClose(hDev);
        }

        IFClose(hIF);
    }

    TLClose(hTL);

    GCCloseLib();

    FreeLibrary(hCTI);

    std::cout << "Finished" << std::endl;

    return 0;
}