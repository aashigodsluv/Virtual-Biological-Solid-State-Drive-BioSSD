# Virtual BioSSD v1.0.0

**Virtual BioSSD** implements the host-Virtual BioSSD is the host-visible software implementation of the Biological Solid-State Drive (BioSSD) architecture through the Universal Molecular Digital Interface (UMDI). It exposes BioSSD as a persistent Windows storage volume while preserving the architecture's logical-block, channel, staging, verification, telemetry and READ/WRITE transaction model.

Download and Quick Start

Windows packaged release

Download the complete Virtual BioSSD v1.0.0 package:

"Download Virtual BioSSD v1.0.0 (.zip)" (https://github.com/aashigodsluv/Virtual-Biological-Solid-State-Drive-BioSSD/releases/download/v1.0.0/Virtual.BioSSD.v1.0.0.zip)

After downloading:

1. Extract the ZIP file.
2. Double-click:

INSTALL_WINDOWS.bat

The installer prepares the required Windows environment, including Python 3.11 x64, WinFsp and WinFSPy components.

3. After installation, double-click:

START_BIOSSD_DRIVE.bat

Virtual BioSSD will automatically select an available drive letter, mount the BioSSD volume in Windows File Explorer, and open the Control & Diagnostics dashboard.

"View all releases" (https://github.com/aashigodsluv/Virtual-Biological-Solid-State-Drive-BioSSD/releases)

## Device profile

* **BioSSD molecular capacity:** 1 gram DNA = **215 PB**
* **Electronic SSD staging/cache:** **1 TB**
* **Effective molecular channels:** **4,096**
* **UMDI logical block:** **256 KiB**
* **READ acceleration configuration:** **4x**
* **WRITE acceleration configuration:** **8x**
* **SSD-class requirement:** **>=500 MB/s**
* **Optimized modeled operating point:** ~**869 MB/s READ**, ~**894 MB/s verified WRITE**

The virtual device is sparse: only data actually written consumes local computer storage.

## Windows: use BioSSD as a drive

### First-time setup

Double-click:

`INSTALL_WINDOWS.bat`

The installer prepares the Windows host-integration environment, including WinFsp and the required Python 3.11 x64 / WinFSPy components.

On some Windows systems, installation of WinFsp or related components may require a restart. A restart may not be necessary on every system; follow any restart prompt presented by Windows or the installer before launching BioSSD.

### Start BioSSD

Double-click:

`START_BIOSSD_DRIVE.bat`

A command window opens immediately. During startup, a blinking cursor may remain visible for roughly 20 seconds while the Virtual BioSSD background components initialize, the filesystem mount is prepared, persisted state is restored, and the local Control & Diagnostics service starts.

Do not close the command window during this initialization period.

BioSSD automatically selects an available Windows drive letter rather than assuming a fixed drive such as B: or D:.

Windows File Explorer will then expose the mounted volume in the form:

`BioSSD (X:)`

where `X:` is the available drive letter selected at launch.

The Control & Diagnostics dashboard opens automatically at:

`http://127.0.0.1:8765`

Keep the command window open while BioSSD is mounted.

Files copied to the BioSSD drive enter the UMDI software path. File writes are committed through UMDI, verified, and marked `MOLECULAR_PERSISTENT` within the Virtual BioSSD state model. Reads reconstruct stored data through the corresponding UMDI READ path.

The dashboard displays mount state, transaction telemetry, molecular capacity usage, electronic staging/cache status, channel health, persistence state, recent operations, and diagnostic information.

## Normal storage interface

Windows File Explorer is the normal host-facing storage interface.

Users may:

* create folders
* copy files to BioSSD
* open files directly from BioSSD
* rename files and folders
* delete files
* retrieve stored files through normal Windows applications

These operations are translated through the UMDI host, protocol, device-software, and Virtual BioSSD layers.

## Developer / Diagnostic UI

The Control & Diagnostics dashboard includes a Developer / Diagnostic UI for direct UMDI testing.

It currently provides:

* **WRITE TO BIOSSD** for direct diagnostic writes
* **STORED OBJECTS** listing
* **READ / DOWNLOAD** for direct recovery of stored objects
* **REFRESH** for updating the stored-object listing
* **INJECT FAULT** for marking a selected molecular channel as faulted
* **CLEAR FAULTS** for restoring simulated channel health

Direct diagnostic READ operations use the same UMDI READ path and integrity-verification logic as the Virtual BioSSD device software.

The Developer / Diagnostic UI provides direct testing and observation of UMDI operations. File Explorer remains the normal storage interface.

Writing files directly through the browser-based **WRITE TO BIOSSD** interface can temporarily slow the host computer for the duration of the transaction, particularly with larger files. In this route, the browser first loads the selected file and transfers it through the local Control & Diagnostics service before the data enters UMDI WRITE handling and Virtual BioSSD persistence:

`Browser upload -> local Control & Diagnostics service -> UMDI WRITE handling -> Virtual BioSSD persistence`

For normal storage operations, Windows File Explorer is the recommended write path:

`Windows File Explorer -> mounted BioSSD drive -> WinFsp -> UMDI Host -> UMDI Protocol -> UMDI Device Software -> Virtual BioSSD`

The File Explorer route avoids the additional browser-side upload stage and is therefore preferable for routine file transfer, especially with larger files.

## Software path

`Windows -> BioSSD drive -> UMDI Host -> UMDI Protocol -> UMDI Device Software -> BioSSD Hardware Interface -> Virtual BioSSD`

The browser dashboard is a **control, diagnostics, and research-observation interface**, not a replacement for the host-visible filesystem.

## Persistent storage

Virtual BioSSD maintains sparse persistent state under the package's `runtime/` directory.

Stored objects and their metadata can therefore remain available between Virtual BioSSD sessions.

The exposed 215 PB value represents the configured molecular-capacity model.

Only data actually written to the Virtual BioSSD consumes local storage.

## Shutdown

Before shutting down Virtual BioSSD, close files or applications currently using the mounted BioSSD volume.

BioSSD may then be unmounted by either:

* pressing `Ctrl+C` in the command window, or
* closing the command window

The mounted BioSSD drive will disappear from Windows File Explorer after the filesystem is stopped.

The browser dashboard may then be closed normally.

## Tests

Core virtual-device test:

`py test_stack.py`

On Windows, after mounting the drive, ordinary File Explorer operations provide a host-visible integration test:

* create folders
* copy files
* rename files
* read files
* delete files
* observe the corresponding UMDI transactions and telemetry

The Developer / Diagnostic UI provides an additional direct-protocol validation path for WRITE, READ, channel-fault injection, and fault recovery.

## Source layout

* `umdi_app.py` - UMDI host/protocol/device/virtual-hardware model and Control & Diagnostics service
* `umdi_windows_drive.py` - Windows host-visible filesystem adapter and automatic drive-letter selection
* `INSTALL_WINDOWS.bat` - Windows dependency and integration installer
* `START_BIOSSD_DRIVE.bat` - Windows Virtual BioSSD launcher
* `requirements-windows.txt` - Windows Python dependency specification
* `include/`, `src/` - C++20 embedded-oriented BioSSD hardware-interface/device-controller core
* `tests/` - C++ round-trip test
* `runtime/` - generated sparse Virtual BioSSD backing state
* `data/` - supporting package data
* `virtual_biossd/` - supporting Virtual BioSSD components
* `PUBLICATION_NOTES.md` - publication architecture and engineering notes

## Engineering relationship

The UMDI Research Simulator explores the architectural requirements for high-speed molecular information storage and identifies optimized combinations of molecular-pathway speed, effective parallelism, logical-block organization, electronic staging, scheduling, and controller behaviour.

The **Virtual BioSSD implements the selected optimized simulator configuration as a host-visible storage architecture for a high-speed molecular medium.**

This creates a direct development path:

`UMDI Research Simulator -> optimized high-speed molecular-storage configuration -> Virtual BioSSD -> physical BioSSD`

The simulator is used to model and optimize the performance architecture. The Virtual BioSSD then implements that architecture at the software and host-interface level, including logical storage, UMDI-controlled READ and WRITE transactions, persistence, integrity verification, channel-state handling, telemetry, and operating-system integration.

The same architecture provides the software foundation for subsequent integration with a physical BioSSD controller and molecular-storage hardware.


## Windows Python compatibility

The Windows drive adapter intentionally uses **Python 3.11 x64** for compatibility with **WinFSPy 0.8.4**.

The installer detects or installs this runtime side-by-side and does not replace a newer system Python such as Python 3.14.

The launcher explicitly invokes Python 3.11 for the Windows BioSSD drive integration.

## Research release

Virtual BioSSD v1.0.0 **provides a reproducible host-visible software implementation of the Virtual BioSSD architecture described in the BioSSD / UMDI research work.**

## Research context

Virtual BioSSD sits on a development line that moves molecular information storage from archival workflows toward addressable, rewritable, automated, and host-visible operation. Key experimental milestones include:

* Tabatabaei Yazdi et al. (2015), *A Rewritable, Random-Access DNA-Based Storage System*, demonstrated random access and rewriting of addressed DNA storage blocks. DOI: https://doi.org/10.1038/srep14138
* Organick et al. (2018), *Random access in large-scale DNA data storage*, stored more than 200 MB across more than 13 million oligonucleotides and demonstrated selective file recovery. DOI: https://doi.org/10.1038/nbt.4079
* Takahashi et al. (2019), *Demonstration of End-to-End Automation of DNA Data Storage*, demonstrated an automated write-store-read system and quantified the large latency gap that remains between molecular workflows and conventional storage operation. DOI: https://doi.org/10.1038/s41598-019-41228-8
* Jia et al. (2026), *DNA Data Storage Architecture via Ligation of Dynamic DNA Bytes*, demonstrated modular DNA storage with CRUD-like operations, hierarchical access, and nanopore-based real-time retrieval. DOI: https://doi.org/10.1002/smtd.202502001

Virtual BioSSD implements the host, control, persistence, integrity, telemetry, and operating-system layers of the BioSSD/UMDI architecture against a virtual molecular-storage endpoint; biochemical synthesis, transport, sensing, and sequencing remain physical-layer operations outside the software package.

## Related software

The performance architecture used by Virtual BioSSD is evaluated in the **Universal Molecular Digital Interface (UMDI) Research Simulator**:

* Repository: https://github.com/aashigodsluv/Universal-molecular-digital-interface-umdi-research-simulator
* Archived release DOI: https://doi.org/10.5281/zenodo.22912687

## License

Virtual BioSSD is released under the **PolyForm Noncommercial License 1.0.0**. Noncommercial research, education, and personal use are permitted under the license terms. Commercial use requires separate permission or licensing from the copyright holder.

Copyright © 2026 Abraham Ikongshul Ashindortiang.

## Citation

Citation metadata is provided in `CITATION.cff`.

