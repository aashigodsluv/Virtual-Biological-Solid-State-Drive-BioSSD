# Virtual BioSSD v1.0.0 - Architecture and Validation Notes

## Scope

This package implements the host-visible **Universal Molecular Digital Interface (UMDI)** control path for the BioSSD architecture with **Virtual BioSSD** implementing the software-side BioSSD hardware endpoint.

On Windows, the operational path is:

`Windows File I/O -> BioSSD drive letter -> WinFsp host adapter -> UMDI Host Software -> UMDI Host-Device Protocol -> UMDI Device Software -> BioSSD Hardware Interface -> Virtual BioSSD`

The mounted BioSSD volume is exposed through an automatically selected available Windows drive letter.

Ordinary Windows applications can access the mounted BioSSD through File Explorer and standard file operations, while the Control & Diagnostics interface observes and interacts with the same UMDI device state.

The diagnostic interface provides direct protocol-level WRITE, READ/DOWNLOAD, telemetry and channel-fault testing in addition to the normal host-visible filesystem path.

## Publication Package

The principal publication files include:

* `INSTALL_WINDOWS.bat`
* `START_BIOSSD_DRIVE.bat`
* `umdi_app.py`
* `umdi_windows_drive.py`
* `requirements-windows.txt`
* `test_stack.py`
* `README.md`
* `PUBLICATION_NOTES.md`

Supporting directories include:

* `runtime/`
* `data/`
* `include/`
* `src/`
* `tests/`
* `virtual_biossd/`

The supplied folder structure should be preserved when the package is extracted.

## Installation

### First-Time Windows Setup

On first use, double-click:

`INSTALL_WINDOWS.bat`

The installer prepares the Windows environment required for host-visible BioSSD operation.

Virtual BioSSD v1.0.0 uses **Python 3.11 x64** for the Windows filesystem adapter and **WinFSPy 0.8.4** for communication with WinFsp.

The installer:

* detects an existing WinFsp installation;
* installs WinFsp through Windows Package Manager where required and available;
* checks for a compatible Python 3.11 x64 runtime;
* installs Python 3.11 where necessary;
* installs the required Python binding;
* verifies that WinFSPy can be imported successfully.

The Python 3.11 runtime is installed alongside newer Python versions and does not require replacement of another system Python installation.

On some Windows systems, WinFsp or related system components may request a restart. Where Windows presents such a request, restart the computer before launching BioSSD. Systems that do not request a restart may proceed directly to launch.

Installation normally needs to be completed only once on a given Windows system.

## Starting Virtual BioSSD

After first-time installation, double-click:

`START_BIOSSD_DRIVE.bat`

A command window opens immediately.

During startup, a blinking cursor may remain visible for approximately 20 seconds while the Virtual BioSSD software stack initializes, persisted state is restored, the filesystem mount is prepared, and the local Control & Diagnostics service starts.

The command window should remain open during initialization and throughout the mounted BioSSD session.

After initialization, the software automatically selects an available Windows drive letter and exposes the volume in File Explorer in the form:

`BioSSD (X:)`

where `X:` represents the available drive letter selected for that session.

The Control & Diagnostics interface then opens in the default browser at:

`http://127.0.0.1:8765`

## Frozen Architectural Profile

* Molecular medium represented: **1 gram DNA**
* Molecular capacity: **215 PB decimal**
* Electronic staging/cache: **1 TB decimal**
* Logical block size: **256 KiB**
* Effective molecular channels: **4,096**
* READ acceleration configuration: **4x**
* WRITE acceleration configuration: **8x**
* SSD-class requirement: **>=500 MB/s**
* Optimized modeled operating point consumed from the research simulator: ~**869 MB/s READ** and ~**894 MB/s verified WRITE**

These values define the selected BioSSD/UMDI operating profile used by the Virtual BioSSD implementation.

## Virtual Capacity and Sparse Persistence

The Virtual BioSSD exposes a logical molecular-storage namespace corresponding to the configured **215 PB molecular capacity**.

The namespace is sparse.

The Windows filesystem can expose the target logical capacity while allocating local storage only for data actually written during operation. The host computer therefore does not need an equivalent amount of physical disk space.

Persistent Virtual BioSSD objects are stored locally together with the metadata required to restore device state across sessions.

The `runtime/` directory contains the sparse backing state used by the current implementation.

## Windows Host Integration

Virtual BioSSD v1.0.0 uses **WinFsp** as the Windows filesystem transport.

WinFsp provides the signed Windows filesystem driver and forwards ordinary Windows file I/O to the user-mode UMDI adapter.

The architecture separates:

* the Windows host filesystem layer;
* WinFsp transport;
* UMDI Host Software;
* UMDI Host-Device Protocol;
* UMDI Device Software;
* the BioSSD Hardware Interface;
* the Virtual BioSSD endpoint.

This separation allows the host-visible UMDI architecture to remain stable while the underlying BioSSD endpoint evolves.

Virtual BioSSD automatically selects an available drive letter rather than depending on a fixed letter such as B: or D:.

An explicit drive letter can still be supplied through the command-line interface where manual selection is required.

## Normal Storage Operation

Windows File Explorer is the normal host-facing storage interface.

Once mounted, BioSSD can be used through conventional file operations including:

* creating folders;
* copying files;
* opening stored files;
* renaming files and folders;
* overwriting files;
* deleting files;
* retrieving files through ordinary Windows applications.

These operations pass through the UMDI software path.

A WRITE transaction progresses through the corresponding control, addressing, staging, channel-allocation, molecular-write representation, verification and persistence states.

A READ transaction progresses through molecular retrieval, sensing/acquisition representation, decoding and error correction, electronic staging and host return.

## Persistent Storage and Integrity

The Virtual BioSSD maintains persistent objects and metadata across sessions.

Stored objects are associated with metadata including file identity, size, logical allocation, persistence state and SHA-256 integrity information.

WRITE operations include readback verification before completion.

READ operations perform integrity verification before returning stored content to the host.

This allows persistence and data integrity to be observed as explicit parts of the UMDI transaction path.

## Control & Diagnostics Interface

The Control & Diagnostics interface provides live access to the Virtual BioSSD and UMDI device state.

It reports:

* mount state;
* selected BioSSD drive letter;
* molecular capacity;
* molecular capacity used;
* electronic staging/cache state;
* effective-channel health;
* READ and WRITE profiles;
* transaction progress;
* molecular-bank selection;
* channel allocation;
* persistence state;
* integrity/ECC state;
* recent UMDI transactions;
* stored-object state;
* device activity.

The dashboard observes the same device state used by the mounted filesystem.

## Developer / Diagnostic Interface

The Developer / Diagnostic interface provides direct access to selected UMDI operations.

Available functions include:

### WRITE TO BIOSSD

Writes a selected file directly through the UMDI protocol path.

Writing files through the browser-based **WRITE TO BIOSSD** interface can temporarily slow the host computer for the duration of the transaction, particularly with larger files. In this route, the browser first loads the selected file and transfers it through the local Control & Diagnostics service before the data enters UMDI WRITE handling and Virtual BioSSD persistence:

`Browser upload -> local Control & Diagnostics service -> UMDI WRITE handling -> Virtual BioSSD persistence`

For normal storage operations, especially larger file transfers, Windows File Explorer is the recommended write path:

`Windows File Explorer -> mounted BioSSD drive -> WinFsp -> UMDI Host -> UMDI Protocol -> UMDI Device Software -> Virtual BioSSD`

The File Explorer route avoids the additional browser-side upload stage.


### STORED OBJECTS

Displays persistent objects registered within the Virtual BioSSD.

### READ / DOWNLOAD

Retrieves a stored object directly through the UMDI READ path and performs integrity verification before returning the file.

### INJECT FAULT

Marks a selected effective molecular channel as faulted for testing degraded-channel behaviour.

### CLEAR FAULTS

Restores all virtual molecular channels to the healthy state.

These controls provide a direct path for testing protocol behaviour independently of normal File Explorer interaction.

## Shutdown and Unmounting

Files currently open from the BioSSD volume should be closed before shutdown.

The Virtual BioSSD can then be stopped by either:

* pressing `Ctrl+C` in the command window; or
* closing the command window.

Both methods terminate the active BioSSD session and remove the mounted volume from Windows File Explorer.

The browser dashboard can then be closed normally.

## Validation

The core software stack can be tested using:

`py test_stack.py`

A basic host-integration validation can then be performed by:

1. launching `START_BIOSSD_DRIVE.bat`;
2. confirming that the dashboard reports a mounted BioSSD drive;
3. confirming that the BioSSD volume appears in File Explorer;
4. copying a test file into the mounted volume;
5. observing the corresponding WRITE transaction;
6. opening or retrieving the file;
7. observing the corresponding READ transaction;
8. renaming the file;
9. deleting the file;
10. confirming the associated transaction history;
11. testing direct diagnostic WRITE where required;
12. testing READ / DOWNLOAD;
13. injecting a channel fault;
14. clearing the fault;
15. stopping and relaunching BioSSD;
16. confirming persistence of undeleted stored objects.

This sequence exercises the principal host-visible, persistence, integrity, diagnostic and channel-state functions of Virtual BioSSD v1.0.0.

## Relationship to the UMDI Research Simulator

The UMDI Research Simulator explores and optimizes the architectural requirements for high-speed molecular information storage, including molecular-pathway speed, effective parallelism, logical-block organization, electronic staging, scheduling, caching, error handling, verification, and controller behaviour.

It also allows researchers to model alternative molecular-storage architectures and investigate what combinations of molecular and electronic improvements can produce faster host-visible READ and WRITE performance.

The **Virtual BioSSD implements the selected optimized simulator configuration as a host-visible storage architecture for a high-speed molecular medium.**

The development relationship is therefore:

`UMDI Research Simulator -> optimized high-speed molecular-storage configuration -> Virtual BioSSD -> physical BioSSD`

The simulator establishes and tests the performance architecture. The Virtual BioSSD implements that architecture at the host, control, transaction, persistence, integrity, channel-state, telemetry, and operating-system integration levels.

This creates a continuous development path from architectural optimization to host-visible implementation and subsequently to physical BioSSD hardware.

## Physical-Device Transition

The Virtual BioSSD endpoint is positioned behind the **BioSSD Hardware Interface**.

The present implementation follows:

`UMDI Host and Protocol -> BioSSD Hardware Interface -> Virtual BioSSD`

A physical implementation can replace the virtual endpoint with:

`UMDI Host and Protocol -> BioSSD Hardware Interface -> Physical BioSSD Controller -> Molecular Hardware`

The physical endpoint may include FPGA/SoC control, molecular READ and WRITE arrays, microfluidic routing, sensing systems, decoding, verification and associated electronics.

The host-visible UMDI architecture remains consistent across that transition.

## Engineering Role of Virtual BioSSD

Virtual BioSSD provides an executable implementation of the UMDI control architecture and BioSSD host-integration model.

It demonstrates:

* host-visible storage integration;
* UMDI-controlled READ and WRITE pathways;
* sparse molecular-capacity representation;
* persistent object storage;
* integrity verification;
* channel-state management;
* transaction telemetry;
* direct diagnostic access;
* adaptive Windows drive mounting;
* a defined interface between host software and a physical BioSSD endpoint.

The software therefore provides a reproducible bridge between the architectural model, the computational performance work, and subsequent physical BioSSD implementation.

## Research Basis

The host-visible architecture builds on a molecular-storage literature that has progressively demonstrated random access, rewriting, larger-scale selective retrieval, end-to-end automation, and increasingly dynamic file-like molecular operations:

1. Tabatabaei Yazdi, S. M. H. et al. **A Rewritable, Random-Access DNA-Based Storage System.** *Scientific Reports* 5, 14138 (2015). https://doi.org/10.1038/srep14138
2. Organick, L. et al. **Random access in large-scale DNA data storage.** *Nature Biotechnology* 36, 242–248 (2018). https://doi.org/10.1038/nbt.4079
3. Takahashi, C. N. et al. **Demonstration of End-to-End Automation of DNA Data Storage.** *Scientific Reports* 9, 4998 (2019). https://doi.org/10.1038/s41598-019-41228-8
4. Jia, L. et al. **DNA Data Storage Architecture via Ligation of Dynamic DNA Bytes.** *Small Methods* 10, e02001 (2026). https://doi.org/10.1002/smtd.202502001

These works establish experimental capabilities and bottlenecks in molecular storage. Virtual BioSSD addresses a different layer of the problem: it implements and exposes the BioSSD/UMDI host, control, transaction, persistence, integrity, telemetry, and operating-system architecture against a virtual molecular-storage endpoint.

## Related UMDI Research Simulator

The optimized operating profile used by Virtual BioSSD is evaluated in the **Universal Molecular Digital Interface (UMDI) Research Simulator**, archived at DOI **10.5281/zenodo.22912687** and maintained at:

https://github.com/aashigodsluv/Universal-molecular-digital-interface-umdi-research-simulator

