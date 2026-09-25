# Anticollisione carroponte — Ouster OS0 Zone Monitor → PLC S7-1500

Applicazione Python (indipendente da CraneScada) che gira sul PC a bordo del
carroponte:

1. riceve i pacchetti UDP **Zone Monitor** del LiDAR Ouster OS0 (firmware
   ≥ 3.2), uno per frame, con lo stato di ogni zona definita nell'app di
   configurazione del sensore;
2. scrive ogni 20 ms lo stato delle zone in un **DB del PLC** S7-1500
   (python-snap7), con un **heartbeat** nei due sensi.

Lo stop del movimento lo decide il PLC; il PC fornisce solo i consensi.

## Principio fail-safe

Il PC scrive `Zone_Free[i]` = **1 solo se la zona è libera E la misura è
valida**. Tutte le anomalie portano il bit a 0, esattamente come un ostacolo:

| Condizione                                         | Effetto                         |
|----------------------------------------------------|---------------------------------|
| Nessun pacchetto dal sensore da > `SENSOR_TIMEOUT_S` | `Sensor_OK`=0, tutte le zone 0 |
| Zona configurata non presente tra le zone live     | `Config_OK`=0, quella zona 0    |
| Hash del set di zone ≠ `EXPECTED_ZONESET_HASH`     | `Config_OK`=0, tutte le zone 0  |
| `error_flags` ≠ 0 sulla zona (se `ERROR_FLAGS_BLOCK`) | quella zona 0                |
| PC/app ferma o rete persa                          | `PC_Heartbeat` fermo → watchdog PLC |
| Arresto pulito dell'app                            | il DB viene azzerato subito     |

Il DB a 0 (es. dopo il download in TIA) significa quindi "movimento non
consentito".

> ⚠️ Il sensore Ouster **non è un dispositivo di sicurezza certificato**
> (SIL/PL) e la catena PC/Ethernet non è deterministica: questa funzione va
> considerata nella valutazione dei rischi come misura aggiuntiva, non come
> funzione di sicurezza certificata.

## Configurazione del sensore (app web Ouster)

1. Definire le zone (es. SINISTRA / CENTRO / DESTRA), in modalità
   **Occupancy**, e annotare il loro **ID**. Per non far scattare le zone
   sulle colonne del capannone, sagomare le zone laterali in modo che non le
   includano (in larghezza/altezza), e regolare la soglia minima di punti e
   di frame della zona.
2. Rendere **live** le zone da monitorare (max 16 zone live).
3. Impostare `udp_dest_zm` = IP del PC e `udp_port_zm` = `ZM_UDP_PORT`
   (default 7504).
4. Consigliato: IP statico sul sensore.

Nota: la soglia "numero minimo di frame" della zona aggiunge latenza (a 10 Hz
ogni frame vale 100 ms). Tempo di reazione indicativo: tempo di frame × frame
di soglia + ≤ 20 ms (scrittura PC) + ciclo PLC.

## Configurazione dell'app

Tutto in `config.py`: IP sensore e PLC, numero DB, elenco zone
(`ZONES`: la posizione in lista = indice nel DB, il valore = ID zona del
sensore), timeout.

Alla prima messa in servizio, avviare l'app e copiare dal log l'**hash del set
di zone** (`Primo pacchetto Zone Monitor: ... hash ...`) in
`EXPECTED_ZONESET_HASH`: così se qualcuno modifica le zone sul sensore l'app
se ne accorge e blocca. Allo stesso modo si può fissare `SENSOR_SERIAL`.

## DB di scambio (TIA Portal)

DB **non ottimizzato** (Proprietà → Attributi → togliere "Accesso ottimizzato
al blocco"), CPU con **"Consenti accesso PUT/GET"** abilitato
(Proprietà CPU → Protezione e sicurezza → Meccanismi di collegamento).

| Offset | Nome               | Tipo                       | Scritto da | Note |
|-------:|--------------------|----------------------------|-----------|------|
| 0.0    | PLC_Heartbeat      | DInt                       | PLC | incrementare es. ogni 100 ms |
| 4.0    | PC_Heartbeat       | DInt                       | PC  | +1 a ogni scrittura (ogni 20 ms) |
| 8.0    | System_OK          | Bool                       | PC  | Sensor_OK AND Config_OK |
| 8.1    | Sensor_OK          | Bool                       | PC  | pacchetti freschi |
| 8.2    | Config_OK          | Bool                       | PC  | zone tutte live, hash ok |
| 8.3    | PLC_Heartbeat_OK   | Bool                       | PC  | il PC vede il PLC vivo |
| 9.0    | Sensor_Alert_Flags | Byte                       | PC  | alert_flags del sensore |
| 10.0   | Zone_Free          | Array[0..15] of Bool       | PC  | **consenso per zona** |
| 12.0   | Zone_Triggered     | Array[0..15] of Bool       | PC  | diagnostica |
| 14.0   | Zone_Valid         | Array[0..15] of Bool       | PC  | diagnostica |
| 16.0   | ZM_Packet_Count    | DInt                       | PC  | |
| 20.0   | Sensor_Frame_Id    | DInt                       | PC  | |
| 24.0   | Packet_Age_ms      | Int                        | PC  | età ultimo pacchetto |
| 26.0   | Fault_Code         | Word                       | PC  | vedi sotto |
| 28.0   | Zone               | Array[0..15] of "ZoneData" | PC  | dettagli per zona |

`ZoneData` (UDT, 32 byte): `Zone_Id` USInt, `Error_Flags` Byte,
`Trigger_Type` USInt (1 occupancy, 2 vacancy), `Trigger_Status` USInt,
`Triggered_Frames` UDInt, `Point_Count` UDInt, `Occlusion_Count` UDInt,
`Invalid_Count` UDInt, `Min_Range_mm` UDInt, `Mean_Range_mm` UDInt,
`Max_Range_mm` UDInt. Dimensione totale DB: 540 byte.

`Fault_Code`: bit0 nessun pacchetto dall'avvio, bit1 timeout sensore, bit2
zona non live, bit3 hash zone diverso, bit4 error_flags su una zona, bit5
pacchetti scartati (CRC/formato) nell'ultimo secondo, bit6 heartbeat PLC fermo.

Il PC non scrive mai i byte 0..3 (PLC_Heartbeat).

### Esempio logica PLC (SCL, in un FB richiamato in OB ciclico)

```pascal
// Heartbeat verso il PC (qui a ogni chiamata; meglio in un OB a tempo, es. 100 ms)
"LIDAR_DB".PLC_Heartbeat := "LIDAR_DB".PLC_Heartbeat + 1;

// Watchdog sull'heartbeat del PC: se non cambia per 300 ms -> PC non vivo
IF "LIDAR_DB".PC_Heartbeat <> #lastPcHeartbeat THEN
    #lastPcHeartbeat := "LIDAR_DB".PC_Heartbeat;
    #wdTimer(IN := FALSE, PT := T#300ms);   // reset del timer
END_IF;
#wdTimer(IN := TRUE, PT := T#300ms);
#pcAlive := NOT #wdTimer.Q;

// Consensi al movimento (1 = consentito)
#consensoSinistra := #pcAlive AND "LIDAR_DB".System_OK AND "LIDAR_DB".Zone_Free[0];
#consensoCentro   := #pcAlive AND "LIDAR_DB".System_OK AND "LIDAR_DB".Zone_Free[1];
#consensoDestra   := #pcAlive AND "LIDAR_DB".System_OK AND "LIDAR_DB".Zone_Free[2];
```

Mascheramenti (es. zona laterale ignorata in corrispondenza di una colonna in
base alla posizione del carroponte) vanno fatti qui, lato PLC.

## Installazione e avvio

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```

Log su console e in `lidar_anticollisione.log` (rotazione 5 × 5 MB): vengono
registrati solo i cambi di stato (zona libera/occupata, sensore perso, heartbeat
PLC fermo, riavvio sensore, modifica delle zone). Sul PC a bordo conviene
avviarla come servizio (es. NSSM su Windows, systemd su Linux) con riavvio
automatico. Verificare che il firewall lasci passare UDP sulla porta
`ZM_UDP_PORT`.

## Prove senza sensore

```bash
python tools/simula_sensore.py --dest 127.0.0.1 --zone 0,1,2
```

invia pacchetti Zone Monitor finti a 10 Hz: scrivere l'ID di una zona + Invio
per occuparla/liberarla, `s` + Invio per simulare il sensore scollegato. Utile
anche per provare la logica PLC al banco.

Test automatici (il parser è verificato su un pacchetto reale OS0-128 fw 3.2):

```bash
python -m pytest tests
```

## File

```
config.py          parametri (sensore, zone, PLC, timeout)
zm_packet.py       parsing del pacchetto UDP Zone Monitor (680 byte, CRC64)
zone_logic.py      valutazione fail-safe delle zone -> immagine da scrivere
plc_link.py        layout del DB, scrittura snap7, lettura heartbeat PLC
sensor_http.py     check diagnostico del sensore via HTTP all'avvio
main.py            loop principale
tools/simula_sensore.py   simulatore del sensore per prove al banco
```
