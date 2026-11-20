# SonosCDPlayer — Parts List

> [!NOTE]
> **Draft / incomplete bill of materials (BOM).** This list is a starting point for a SonosCDPlayer build, not a complete or independently verified shopping list. The linked products are examples, not guarantees of compatibility. Confirm electrical specifications, mechanical fit, and component availability before purchasing or assembling parts.

## Parts to purchase

| Component | Specification / suggested part | Qty. | Notes | Example product |
| --- | --- | ---: | --- | --- |
| Raspberry Pi | Raspberry Pi 4 Model B or newer | 1 | A model with at least 2 GB of RAM is recommended. | [Product link](https://www.amazon.com/dp/B0HLX6MJ6B) |
| CPU heatsink | Heatsink suitable for the Raspberry Pi CPU | 1 | Optional, but recommended. | [Product link](https://www.amazon.com/dp/B083QRVJ5T) |
| OLED/LCD display | Waveshare 1.5-inch OLED module | 1 | If using another display, consult the project's compatible-display list. Do not assume that all OLED or LCD modules are supported. | [Product link](https://www.amazon.com/dp/B079KRC9X3) |
| microSD card | 16 GB or larger | 1 | A separate microSD card reader/writer may be needed to prepare the card. [Example reader/writer](https://www.amazon.com/dp/B071VMH96B). | [Product link](https://www.amazon.com/dp/B08HYDH7JF) |
| Powered USB hub | Waveshare Industrial 4-Port USB 3.2 Gen 1 Hub (SKU:27837) | 1 | The hub accepts 7–36 V DC input. The listed 12 V / 4 A power supply can be used directly without modification. Confirm connector polarity and the total power budget, including power delivered to the Raspberry Pi and optical drive. | [Product link](https://www.amazon.com/dp/B09J3FDP69) |
| Optical drive | CD-compatible optical drive with a Slim SATA connector | 1 | A slot-loading drive (for example, the Panasonic UL265) may be preferable to a tray-loading model, although suitable slot-loading drives can be difficult to source. Confirm drive and adapter compatibility. | [Product link](https://www.amazon.com/dp/B075D9J83G) |
| USB-to-Slim-SATA adapter | Delock USB 3.0 to Slim SATA Converter, part no. 64048 | 1 | Select an adapter known to work with your chosen optical drive under Raspberry Pi OS/Linux. Some USB-to-SATA adapters do not fully support optical drives. | [Product link](https://www.amazon.com/dp/B07WS5YSSZ) |
| USB power cable | USB-A to USB-C, approximately 30 cm (1 ft), rated for at least 15 W | 1 | Intended to supply power to the Raspberry Pi from the powered USB hub. USB 2.0 data capability is sufficient for this power-only connection. Verify the actual current rating and power arrangement before use. | [Product link](https://www.amazon.com/dp/B0F67C8BRD) |
| DC power supply | 12 V / 4 A; 5.5 × 2.1 mm barrel connector | 1 | Compatible with the listed USB hub's 7–36 V DC input range; no voltage converter or modification is required. Confirm barrel connector fit and polarity, the overall load, and compatibility with local mains voltage and frequency. | [Product link](https://www.amazon.com/dp/B0F23MSF2G) |
| Panel-mount Ethernet extension | RJ45 Cat 6 extension, approximately 30 cm (1 ft), with panel-mount socket | 1 | Verify that the connector and cable use standard Ethernet-compatible wiring. An “8P8C” designation alone does not establish Ethernet compatibility. | [Product link](https://www.amazon.com/dp/B0H7LYCKGN) |
| Panel-mount DC power connector | 5.5 × 2.1 mm female barrel connector with pre-soldered leads, rated for 60 W | 1 | If you prefer not to solder power leads directly to the hub, consider an extension-style assembly with a matching male barrel plug. Verify voltage, current rating, and polarity. | [Product link](https://www.amazon.com/dp/B0HRRS6K6B) |
| Input PCB | Project-specific input circuit board; see the repository's PCB files | 1 | Build the board yourself or have it manufactured from the provided design files. The intended assembly includes an ambient-light sensor, a rotary encoder, and nine tactile push buttons. Check whether components are included or must be sourced separately. | — |
| GPIO ribbon cable | 40-pin, 2.54 mm-pitch ribbon cable with female IDC connectors at both ends | 1 | Connects the Raspberry Pi GPIO header to the corresponding hardware interface. Check the pin orientation before connecting. | [Product link](https://www.amazon.com/dp/B0GPMZLKVB) |
| M3 heat-set threaded inserts | Threaded brass inserts for M3 screws | TBD | Installation requires a suitable soldering iron or heat-set insert press. Select insert dimensions to match the printed parts. | [Product link](https://www.amazon.com/dp/B0HC7287X3) |

## Parts to 3D-print

| Part | Qty. | Notes |
| --- | ---: | --- |
| Optical drive slim sleeve mount | TBD | Print file, material, and printing requirements have not yet been specified in the source list. |

## Before you build

> [!IMPORTANT]
> **Power distribution:** The USB hub supports 7–36 V DC input, so the listed 12 V / 4 A adapter can power the hub directly without modification. Check barrel connector fit and polarity, and verify that the hub and its downstream power connections can safely supply the combined load of the Raspberry Pi and optical drive.

**Additional compatibility and assembly checks:**
- **Optical drive compatibility:** Confirm that the drive, Slim SATA adapter, and USB hub work together under Linux. The product links are not a compatibility test record.
- **Display compatibility:** Refer to the project's currently supported display controllers and configuration requirements when selecting an alternative display.
- **PCB assembly:** Confirm whether the input PCB is supplied fully assembled or whether the sensor, encoder, switches, and any other components must be purchased separately.
- **Mechanical assembly:** Check enclosure dimensions, mounting hardware, insert sizes, and printed-part requirements against the chosen components.

## List status

> [!NOTE]
> This is an **incomplete draft** based on the initial CSV. Missing quantities are marked **TBD**, and missing specifications are described rather than guessed. Additional parts, fasteners, cables, and printed components will be required.
