#pragma once
#include "types.hpp"
namespace umdi {
class BioSSDHardwareInterface {
public:
    virtual ~BioSSDHardwareInterface() = default;
    virtual const BioSSDProfile& profile() const = 0;
    virtual Result select_bank(std::uint32_t bank) = 0;
    virtual Result allocate_channels(std::size_t count) = 0;
    virtual Result release_channels() = 0;
    virtual Result write(LogicalBlock block, const Bytes& data) = 0;
    virtual Result read(LogicalBlock block, Bytes& out) = 0;
    virtual Result verify(LogicalBlock block, const Bytes& expected) = 0;
    virtual PersistenceState persistence_state(LogicalBlock block) const = 0;
    virtual Telemetry telemetry() const = 0;
    virtual Result reset() = 0;
};
}
