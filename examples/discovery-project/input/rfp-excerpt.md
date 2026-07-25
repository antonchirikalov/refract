# RFP excerpt — "Goods receiving digitalization" (sections 3-5)

## 3. Scope

3.1 The Supplier shall deliver a mobile application for Android handheld terminals
enabling barcode-based goods receiving at the Buyer's distribution centre.

3.2 The application shall validate scanned pallet identifiers against the expected
delivery contents obtained from the Buyer's ERP system and shall flag discrepancies
in quantity or article number to the operator before the receipt is confirmed.

3.3 An integration layer shall be delivered to mediate between the mobile application
and the Buyer's ERP (SOAP, on-premise). Direct modification of the ERP is out of scope.

3.4 A reporting facility shall produce a monthly discrepancy report in a spreadsheet
format, exportable without interactive access to the system.

3.5 Out of scope: shipping/outbound processes, inventory counting, integration with
the Buyer's transport management system, and any hardware supply.

## 4. Non-functional requirements

4.1 Availability: the receiving flow shall remain operable during loss of network
connectivity for at least 4 continuous hours, with automatic synchronisation upon
reconnection. Conflicting updates shall be surfaced to a supervisor rather than
resolved silently.

4.2 Performance: a scan-to-feedback cycle shall not exceed 2 seconds under normal
network conditions and shall not block the operator when offline.

4.3 Capacity: the solution shall support at least 300 receipt lines per day with a
peak factor of 2.5, and 40 concurrent handheld devices.

4.4 Localisation: the operator interface shall be available in Polish. Master data
originating from the ERP shall be displayed as received, without translation.

4.5 Data residency: all personal data, including operator identities and audit trails
attributing actions to named individuals, shall be stored within the territory of the
Buyer's country. Cloud services outside that territory may not process such data.

4.6 Security: operators shall authenticate with their existing corporate accounts.
Device loss shall not expose stored receipt data.

## 5. Acceptance

5.1 Pilot acceptance: two weeks of productive use on a single dock with a discrepancy
detection rate confirmed against a manual control count, error rate below 1% of lines.

5.2 Full acceptance follows rollout to the remaining docks and one full monthly
reporting cycle.
