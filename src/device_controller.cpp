#include "umdi/device_controller.hpp"
#include <algorithm>
namespace umdi {
Result DeviceController::write_block(LogicalBlock block,const Bytes& data){
    if(data.size()>hw_.profile().logical_block_size) return {false,"block too large"};
    // Bank selection is deterministic behind the BioSSD hardware interface.
    if(auto r=hw_.select_bank(static_cast<std::uint32_t>(block)); !r.ok) return r;
    if(auto r=hw_.allocate_channels(hw_.profile().effective_channels); !r.ok) return r;
    if(auto r=hw_.write(block,data); !r.ok){ hw_.release_channels(); return r; }
    auto v=hw_.verify(block,data);
    hw_.release_channels();
    return v.ok ? Result{true,"WRITE verified and molecular-persistent"}:v;
}
Result DeviceController::read_block(LogicalBlock block,Bytes& out){
    if(auto r=hw_.select_bank(static_cast<std::uint32_t>(block)); !r.ok) return r;
    if(auto r=hw_.allocate_channels(hw_.profile().effective_channels); !r.ok) return r;
    auto r=hw_.read(block,out); hw_.release_channels(); return r;
}
}
