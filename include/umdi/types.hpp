#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace umdi {
constexpr std::size_t kLogicalBlockSize = 256 * 1024; // 256 KiB architecture target
constexpr std::size_t kEffectiveChannels = 4096;
constexpr double kReadPathAcceleration = 4.0;
constexpr double kWritePathAcceleration = 8.0;
constexpr double kSSDClassTargetMBps = 500.0;
constexpr double kOptimizedReadMBps = 869.0;   // modeled 1 GiB cold READ operating point
constexpr double kOptimizedWriteMBps = 894.0;  // modeled 1 GiB verified WRITE operating point
constexpr double kOptimizedRead1GiBSeconds = 1.235;
constexpr double kOptimizedWrite1GiBSeconds = 1.201;
constexpr double kOptimizedRoundTrip1GiBSeconds = 2.436;

using Bytes = std::vector<std::uint8_t>;
using LogicalBlock = std::uint64_t;

enum class Opcode { Read, Write, Verify, GetStatus, GetTelemetry, Reset };
enum class TxState { Queued, Addressed, Allocated, Staged, Executing, Verifying, Persistent, Complete, Failed };
enum class PersistenceState { Absent, ElectronicStaged, MolecularPersistent, VerificationFailed };

struct Result { bool ok; std::string message; };

struct BioSSDProfile {
    std::size_t logical_block_size = kLogicalBlockSize;
    std::size_t effective_channels = kEffectiveChannels;
    double read_path_acceleration = kReadPathAcceleration;
    double write_path_acceleration = kWritePathAcceleration;
    double read_throughput_mbps = kOptimizedReadMBps;
    double write_throughput_mbps = kOptimizedWriteMBps;
    double ssd_class_target_mbps = kSSDClassTargetMBps;
};

struct Telemetry {
    std::size_t allocated_channels = 0;
    std::uint32_t selected_bank = 0;
    std::size_t persistent_blocks = 0;
    std::uint64_t bytes_read = 0;
    std::uint64_t bytes_written = 0;
    std::uint64_t reads = 0;
    std::uint64_t writes = 0;
    std::uint64_t verifies = 0;
    double last_read_seconds = 0.0;
    double last_write_seconds = 0.0;
    bool meets_ssd_class_read_target = false;
    bool meets_ssd_class_write_target = false;
};
}
