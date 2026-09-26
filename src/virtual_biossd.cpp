#include "umdi/virtual_biossd.hpp"
#include <chrono>
#include <thread>

namespace umdi {
VirtualBioSSD::VirtualBioSSD(BioSSDProfile profile) : profile_(profile) {
    telemetry_.meets_ssd_class_read_target = profile_.read_throughput_mbps >= profile_.ssd_class_target_mbps;
    telemetry_.meets_ssd_class_write_target = profile_.write_throughput_mbps >= profile_.ssd_class_target_mbps;
}

double VirtualBioSSD::service_seconds(std::size_t bytes, double throughput_mbps) {
    if (throughput_mbps <= 0.0) return 0.0;
    return static_cast<double>(bytes) / (throughput_mbps * 1'000'000.0);
}

void VirtualBioSSD::emulate_service_time(double seconds) {
    if (seconds > 0.0) std::this_thread::sleep_for(std::chrono::duration<double>(seconds));
}

Result VirtualBioSSD::select_bank(std::uint32_t bank) {
    bank_ = bank;
    telemetry_.selected_bank = bank;
    return {true,"bank selected"};
}

Result VirtualBioSSD::allocate_channels(std::size_t count) {
    if (count == 0) return {false,"at least one channel is required"};
    if (count > profile_.effective_channels) return {false,"channel request exceeds BioSSD profile"};
    allocated_ = count;
    telemetry_.allocated_channels = count;
    return {true,"channels allocated"};
}

Result VirtualBioSSD::release_channels() {
    allocated_ = 0;
    telemetry_.allocated_channels = 0;
    return {true,"channels released"};
}

Result VirtualBioSSD::write(LogicalBlock block,const Bytes& data) {
    if (!allocated_) return {false,"no channels allocated"};
    if (data.size() > profile_.logical_block_size) return {false,"logical block exceeds BioSSD profile"};

    auto& stored = storage_[block];
    stored.state = PersistenceState::ElectronicStaged;
    const double seconds = service_seconds(data.size(), profile_.write_throughput_mbps);
    emulate_service_time(seconds);
    stored.data = data;
    stored.state = PersistenceState::MolecularPersistent;

    telemetry_.writes++;
    telemetry_.bytes_written += data.size();
    telemetry_.last_write_seconds = seconds;
    telemetry_.persistent_blocks = storage_.size();
    return {true,"molecular WRITE complete"};
}

Result VirtualBioSSD::read(LogicalBlock block,Bytes& out) {
    if (!allocated_) return {false,"no channels allocated"};
    auto it = storage_.find(block);
    if (it == storage_.end() || it->second.state != PersistenceState::MolecularPersistent)
        return {false,"persistent block not found"};

    const double seconds = service_seconds(it->second.data.size(), profile_.read_throughput_mbps);
    emulate_service_time(seconds);
    out = it->second.data;
    telemetry_.reads++;
    telemetry_.bytes_read += out.size();
    telemetry_.last_read_seconds = seconds;
    return {true,"molecular READ complete"};
}

Result VirtualBioSSD::verify(LogicalBlock block,const Bytes& expected) {
    telemetry_.verifies++;
    auto it = storage_.find(block);
    if (it != storage_.end() && it->second.data == expected &&
        it->second.state == PersistenceState::MolecularPersistent)
        return {true,"molecular persistence verified"};
    if (it != storage_.end()) it->second.state = PersistenceState::VerificationFailed;
    return {false,"verification failed"};
}

PersistenceState VirtualBioSSD::persistence_state(LogicalBlock block) const {
    auto it = storage_.find(block);
    return it == storage_.end() ? PersistenceState::Absent : it->second.state;
}

Result VirtualBioSSD::reset() {
    storage_.clear(); allocated_=0; bank_=0; telemetry_ = {};
    telemetry_.meets_ssd_class_read_target = profile_.read_throughput_mbps >= profile_.ssd_class_target_mbps;
    telemetry_.meets_ssd_class_write_target = profile_.write_throughput_mbps >= profile_.ssd_class_target_mbps;
    return {true,"reset"};
}
}
