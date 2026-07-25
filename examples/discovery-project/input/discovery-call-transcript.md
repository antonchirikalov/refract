# Discovery call — warehouse operations platform (transcript, 2026-06-12)

Participants: operations lead (customer), IT manager (customer), analyst (vendor).

**Analyst:** Let's start with what breaks today.

**Ops lead:** Receiving. A truck arrives, the driver hands over a paper packing list,
and our people key it into the ERP by hand. Two of the three shifts do it wrong at
least once a day — wrong pallet count, wrong SKU. We only find out at the monthly
stock count, and by then nobody remembers which pallet it was.

**Analyst:** How many receipts a day?

**Ops lead:** Around 120 lines across 30-40 trucks. Peaks before holidays, maybe double.

**IT manager:** The ERP is on-prem, version from 2019. It has a SOAP API but we have
never used it from outside the building. Upgrading it this year is not on the table,
the budget is locked.

**Analyst:** What would "fixed" look like?

**Ops lead:** The receiver scans a barcode on the pallet with a handheld, the system
already knows what should be on that truck, and it tells him right there if the count
does not match. No paper, no typing. And I want to see, in the evening, which receipts
had mismatches and who handled them.

**IT manager:** The handhelds we have are Android, Zebra TC21, about 25 of them. Wi-Fi
covers the docks but drops in the freezer aisle — that is a known dead spot we are not
going to fix soon.

**Analyst:** So the scanner app has to survive losing connectivity?

**Ops lead:** Yes. If it stops working when the Wi-Fi blinks, the guys go back to paper
in a week and we have wasted the money.

**IT manager:** Also: our people are not all comfortable in English. The interface needs
to be in Polish, and the ERP master data is a mix — descriptions are Polish, unit codes
are English.

**Analyst:** Who else needs to see this data?

**Ops lead:** Finance wants the mismatch report as a file they can open in Excel, monthly.
They will not log into a new system, do not even try.

**IT manager:** And whatever you build, it cannot store personal data outside our
country. Legal is firm on that. The receiver names are personal data.

**Analyst:** Timeline?

**Ops lead:** Pilot on one dock before the November peak. If it works there, the other
two docks after New Year.
