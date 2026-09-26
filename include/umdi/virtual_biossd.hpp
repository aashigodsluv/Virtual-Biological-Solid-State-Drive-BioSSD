#pragma once
#include "hardware_interface.hpp"
#include <unordered_map>

namespace umdi {
class VirtualBioSSD final : public BioSSDHardwareInterface {
    struct StoredBlock {
        Bytes data;
        PersistenceState state = PersistenceState::Absent;
    };

    BioSSDProfile profile_{};
    std::unordered_map<LogicalBlock, StoredBlock> storage_;
    std::size_t allocated_ = 0;
    std::uint32_t bank_ = 0;
    Telemetry telemetry_{};

    static double service_seconds(std::size_t bytes, double throughput_mbps);
    static void emulate_service_time(double seconds);

public:
    explicit VirtualBioSSD(BioSSDProfile profile = {});
    const BioSSDProfile& profile() const override { return profile_; }
    Result select_bank(std::uint32_t bank) override;
    Result allocate_channels(std::size_t count) override;
    Result release_channels() override;
    Result write(LogicalBlock block, const Bytes& data) override;
    Result read(LogicalBlock block, Bytes& out) override;
    Result verify(LogicalBlock block, const Bytes& expected) override;
    PersistenceState persistence_state(LogicalBlock block) const override;
    Telemetry telemetry() const override { return telemetry_; }
    Result reset() override;
};
}
