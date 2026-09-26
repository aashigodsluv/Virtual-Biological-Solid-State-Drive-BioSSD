#include "umdi/device_controller.hpp"
#include "umdi/virtual_biossd.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
int main(){
    umdi::VirtualBioSSD hw; umdi::DeviceController d(hw);
    assert(hw.profile().effective_channels == 4096);
    assert(hw.profile().logical_block_size == 256*1024);
    assert(hw.profile().read_path_acceleration == 4.0);
    assert(hw.profile().write_path_acceleration == 8.0);
    assert(hw.profile().read_throughput_mbps >= 500.0);
    assert(hw.profile().write_throughput_mbps >= 500.0);

    umdi::Bytes input(umdi::kLogicalBlockSize);
    for(std::size_t i=0;i<input.size();++i) input[i]=static_cast<std::uint8_t>(i%251);
    umdi::Bytes output;
    auto w=d.write_block(42,input); assert(w.ok);
    assert(hw.persistence_state(42)==umdi::PersistenceState::MolecularPersistent);
    auto r=d.read_block(42,output); assert(r.ok);
    assert(input==output);
    auto t=hw.telemetry();
    assert(t.writes==1 && t.reads==1 && t.verifies==1);
    assert(t.meets_ssd_class_read_target && t.meets_ssd_class_write_target);
    std::cout<<"Virtual BioSSD profile WRITE -> VERIFY -> READ: PASS\n";
}
