# Anticollisione carroponte — contesto progetto

## Obiettivo
App Python che gira su un PC a bordo di un carroponte: riceve dal LiDAR
**Ouster OS0** (firmware >= 3.2) i pacchetti UDP **Zone Monitor** e scrive lo
stato delle zone in un DB del PLC **Siemens S7-1500F** (python-snap7), per
evitare la collisione con un altro carroponte che si muove trasversalmente.

## Decisioni prese (da rispettare)
- Lo stop del movimento lo decide il **PLC**; il PC fornisce solo consensi.
- **Fail-safe**: il PC scrive `Zone_Free[i]` = 1 solo se zona libera E misura
  valida; ogni anomalia (sensore muto, zona non live, hash zone diverso,
  error_flags) -> 0. DB a zero = movimento non consentito.
- **Heartbeat** nei due sensi: `PC_Heartbeat` (PC->PLC, watchdog lato PLC) e
  `PLC_Heartbeat` (PLC->PC, byte 0..3, il PC non li scrive mai).
- Zone mappate per **ID zona** del sensore (non per slot del pacchetto);
  almeno 3 zone: SINISTRA / CENTRO / DESTRA. Possibili falsi positivi: colonne
  del capannone ai lati -> sagomare le zone o mascherare lato PLC.
- PC collegato direttamente alla rete del PLC. Progetto indipendente da
  CraneScada (da cui riprende solo lo stile di `plc_comm/s7_client.py`).
- Formato pacchetto ricavato da ouster-sdk (`ouster_core/src/parsing.cpp`) e
  verificato su un pacchetto reale OS0-128 fw 3.2 (`tests/data/`).

## Stato
Implementato e testato con PLC simulato (snap7 server) + `tools/simula_sensore.py`.
Da fare: validazione con sensore e PLC reali; layout DB e logica PLC in
`README.md`. L'utente lavora in italiano e preferisce modifiche iterative.
