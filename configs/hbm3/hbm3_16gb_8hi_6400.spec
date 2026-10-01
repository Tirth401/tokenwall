tokenwall-dramspec 1
# generated 2026-09-30 by scripts/export_dram_spec.py from Ramulator 2.1 72427a1
# presets HBM3_16Gb_8hi HBM3_6400Mbps; overrides none; latencies in half-CK ticks
standard HBM3
tick_ps_numerator 625
tick_multiplier 2
levels Channel PseudoChannel Sid BankGroup Bank Row Column
counts 1 2 2 4 4 16384 256
commands ACT PREpb PREab RD WR RDA WRA REFab REFpb RFMab RFMpb
command_cycles 3 1 1 2 2 2 2 1 1 1 1
row_commands ACT PREpb PREab REFab REFpb RFMab RFMpb
column_commands RD WR RDA WRA
read_latency 44
tx_bytes 32
timing rate 6400
timing nBL 4
timing nCL 40
timing nRCDRD 62
timing nRCDWR 30
timing nRP 52
timing nRAS 90
timing nRC 142
timing nWR 66
timing nRTP 18
timing nCWL 20
timing nCCDS 4
timing nCCDL 8
timing nCCDR 6
timing nRRDS 8
timing nRRDL 10
timing nWTRS 14
timing nWTRL 20
timing nRTW 34
timing nFAW 48
timing nPPD 4
timing nRFC 1120
timing nRFCpb 640
timing nRFMab 1120
timing nRFMpb 640
timing nRREFD 26
timing nREFI 12480
timing nREFIpb 390
timing tCK_ps 312
constraint 0 0 3 1 0 -1 Channel:bus(ACT) P 0 F 0 1 2 7 8 9 10
constraint 1 0 2 1 0 -1 Channel:bus(RD/WR/RDA/WRA) P 3 4 5 6 F 3 4 5 6
constraint 2 1 4 1 0 -1 PseudoChannel:nBL P 3 5 F 3 5
constraint 3 1 4 1 0 -1 PseudoChannel:nBL P 4 6 F 4 6
constraint 4 1 34 1 0 -1 PseudoChannel:nRTW P 3 5 F 4 6
constraint 5 1 38 1 0 -1 PseudoChannel:nCWL+nBL+nWTRS P 4 6 F 3 5
constraint 6 1 19 1 0 -1 PseudoChannel:nRTP P 3 5 F 2
constraint 7 1 91 1 0 -1 PseudoChannel:nCWL+nBL+nWR P 4 6 F 2
constraint 8 1 8 1 0 -1 PseudoChannel:nRRDS P 0 F 0
constraint 9 1 46 4 0 0 PseudoChannel:nFAW P 0 8 10 F 0
constraint 10 1 48 4 0 0 PseudoChannel:nFAW P 0 8 10 F 8 10
constraint 11 1 92 1 0 -1 PseudoChannel:nRAS P 0 F 2
constraint 12 1 50 1 0 -1 PseudoChannel:nRP P 2 F 0
constraint 13 1 4 1 0 -1 PseudoChannel:nPPD P 1 2 F 1 2
constraint 14 1 144 1 0 -1 PseudoChannel:nRC P 0 F 7
constraint 15 1 52 1 0 -1 PseudoChannel:nRP P 1 2 F 7
constraint 16 1 71 1 0 -1 PseudoChannel:nRP+nRTP P 5 F 7
constraint 17 1 143 1 0 -1 PseudoChannel:nCWL+nBL+nWR+nRP P 6 F 7
constraint 18 1 1118 1 0 -1 PseudoChannel:nRFC P 7 F 0
constraint 19 1 1120 1 0 -1 PseudoChannel:nRFC P 7 F 2 7 8 9 10
constraint 20 1 26 1 0 -1 PseudoChannel:nRREFD P 8 F 8 10
constraint 21 1 24 1 0 -1 PseudoChannel:nRREFD P 8 F 0
constraint 22 1 640 1 0 -1 PseudoChannel:nRFCpb P 8 F 7 9
constraint 23 1 10 1 0 -1 PseudoChannel:nRRDS P 0 F 8
constraint 24 1 144 1 0 -1 PseudoChannel:nRC P 0 F 9
constraint 25 1 52 1 0 -1 PseudoChannel:nRP P 1 2 F 9
constraint 26 1 71 1 0 -1 PseudoChannel:nRP+nRTP P 5 F 9
constraint 27 1 143 1 0 -1 PseudoChannel:nCWL+nBL+nWR+nRP P 6 F 9
constraint 28 1 1118 1 0 -1 PseudoChannel:nRFMab P 9 F 0
constraint 29 1 1120 1 0 -1 PseudoChannel:nRFMab P 9 F 2 7 8 9 10
constraint 30 1 26 1 0 -1 PseudoChannel:nRREFD P 10 F 8 10
constraint 31 1 24 1 0 -1 PseudoChannel:nRREFD P 10 F 0
constraint 32 1 640 1 0 -1 PseudoChannel:nRFMpb P 10 F 7 9
constraint 33 1 10 1 0 -1 PseudoChannel:nRRDS P 0 F 10
constraint 34 2 4 1 0 -1 Sid:nCCDS P 3 5 F 3 5
constraint 35 2 4 1 0 -1 Sid:nCCDS P 4 6 F 4 6
constraint 36 2 6 1 1 -1 Sid:nCCDR P 3 5 F 3 5
constraint 37 3 8 1 0 -1 BankGroup:nCCDL P 3 5 F 3 5
constraint 38 3 8 1 0 -1 BankGroup:nCCDL P 4 6 F 4 6
constraint 39 3 44 1 0 -1 BankGroup:nCWL+nBL+nWTRL P 4 6 F 3 5
constraint 40 3 10 1 0 -1 BankGroup:nRRDL P 0 F 0
constraint 41 3 12 1 0 -1 BankGroup:nRRDL P 0 F 8 10
constraint 42 3 8 1 0 -1 BankGroup:nRRDL P 8 10 F 0
constraint 43 4 142 1 0 -1 Bank:nRC P 0 F 0
constraint 44 4 63 1 0 -1 Bank:nRCDRD P 0 F 3 5
constraint 45 4 31 1 0 -1 Bank:nRCDWR P 0 F 4 6
constraint 46 4 92 1 0 -1 Bank:nRAS P 0 F 1
constraint 47 4 50 1 0 -1 Bank:nRP P 1 F 0
constraint 48 4 19 1 0 -1 Bank:nRTP P 3 F 1
constraint 49 4 91 1 0 -1 Bank:nCWL+nBL+nWR P 4 F 1
constraint 50 4 69 1 0 -1 Bank:nRTP+nRP P 5 F 0
constraint 51 4 141 1 0 -1 Bank:nCWL+nBL+nWR+nRP P 6 F 0
constraint 52 4 640 1 0 -1 Bank:nRFCpb P 8 F 8 10
constraint 53 4 638 1 0 -1 Bank:nRFCpb P 8 F 0
constraint 54 4 144 1 0 -1 Bank:nRC P 0 F 8
constraint 55 4 52 1 0 -1 Bank:nRP P 1 F 8
constraint 56 4 640 1 0 -1 Bank:nRFMpb P 10 F 8 10
constraint 57 4 638 1 0 -1 Bank:nRFMpb P 10 F 0
constraint 58 4 144 1 0 -1 Bank:nRC P 0 F 10
constraint 59 4 52 1 0 -1 Bank:nRP P 1 F 10
