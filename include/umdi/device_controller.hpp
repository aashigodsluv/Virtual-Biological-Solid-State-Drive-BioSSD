#pragma once
#include "hardware_interface.hpp"
namespace umdi {
class DeviceController {
    BioSSDHardwareInterface& hw_;
public:
    explicit DeviceController(BioSSDHardwareInterface& hw) : hw_(hw) {}
    Result write_block(LogicalBlock block, const Bytes& data);
    Result read_block(LogicalBlock block, Bytes& out);
};
}
