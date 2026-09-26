#include "umdi/device_controller.hpp"
#include "umdi/virtual_biossd.hpp"
#include <iomanip>
#include <iostream>

int main(){
    umdi::VirtualBioSSD biossd;
    umdi::DeviceController device(biossd);
    umdi::Bytes input(umdi::kLogicalBlockSize);
    for(std::size_t i=0;i<input.size();++i) input[i]=static_cast<std::uint8_t>(i%251);
    umdi::Bytes output;
    auto w=device.write_block(0,input);
    auto r=device.read_block(0,output);
    auto t=biossd.telemetry();

    std::cout << "UMDI Software Stack / Virtual BioSSD\n"
              << "Profile: " << biossd.profile().effective_channels << " channels, "
              << biossd.profile().logical_block_size/1024 << " KiB blocks, "
              << biossd.profile().read_path_acceleration << "x READ, "
              << biossd.profile().write_path_acceleration << "x WRITE\n"
              << "Configured profile: READ " << biossd.profile().read_throughput_mbps
              << " MB/s, WRITE " << biossd.profile().write_throughput_mbps << " MB/s\n"
              << "WRITE: " << w.message << "\nREAD: " << r.message
              << "\nIntegrity: " << (input==output?"PASS":"FAIL")
              << "\nPersistence: " << (biossd.persistence_state(0)==umdi::PersistenceState::MolecularPersistent?"MOLECULAR_PERSISTENT":"NOT_PERSISTENT")
              << "\nVirtual service time: WRITE " << std::fixed << std::setprecision(3)
              << t.last_write_seconds*1000.0 << " ms, READ " << t.last_read_seconds*1000.0 << " ms\n";
    return (w.ok && r.ok && input==output)?0:1;
}
