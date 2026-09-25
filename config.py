"""
Configurazione dell'applicazione anticollisione LiDAR (Ouster OS0 + Zone Monitor).
Modificare i valori qui sotto con i parametri reali dell'impianto.
"""

# --- Sensore Ouster ---
SENSOR_HOST = "192.168.1.50"   # IP del sensore (usato solo per il check HTTP all'avvio)

# Porta UDP su cui il sensore invia i pacchetti Zone Monitor. Deve coincidere
# con `udp_port_zm` configurato sul sensore, e `udp_dest_zm` deve essere l'IP
# di questo PC (vedi README).
ZM_UDP_PORT = 7504
ZM_UDP_BIND_IP = "0.0.0.0"     # scheda di rete su cui ascoltare ("0.0.0.0" = tutte)

# Numero di serie del sensore: se impostato, i pacchetti di altri sensori
# (es. quello dell'altro carroponte, se finisse sulla stessa rete) vengono
# scartati. None = accetta qualsiasi sensore.
SENSOR_SERIAL: int | None = None

# Se nessun pacchetto Zone Monitor valido arriva entro questo tempo, il
# sensore è considerato perso -> tutte le zone NON libere. Il sensore manda un
# pacchetto per frame (10 o 20 Hz, cioè ogni 100 o 50 ms).
SENSOR_TIMEOUT_S = 0.3

# Verifica del CRC64 in coda al pacchetto (consigliato lasciarlo attivo).
VERIFY_CRC = True

# Hash (hex, 64 caratteri) del set di zone attive, come riportato nel
# pacchetto. Se impostato, un pacchetto con hash diverso (zone modificate
# dall'app di configurazione senza aggiornare qui) porta Config_OK = 0 e
# tutte le zone NON libere. Il valore corrente viene scritto nel log
# all'avvio: copiarlo qui a fine messa in servizio. None = nessuna verifica.
EXPECTED_ZONESET_HASH: str | None = None

# Zone da monitorare. La POSIZIONE nella lista è l'indice nel DB del PLC
# (Zone_Free[0], Zone_Free[1], ...), lo `zone_id` è l'ID della zona definita
# nell'app di configurazione del sensore (deve essere tra le zone "live").
# Max 16 zone (limite del sensore sulle zone live).
ZONES: list[tuple[str, int]] = [
    ("SINISTRA", 0),
    ("CENTRO", 1),
    ("DESTRA", 2),
]

# Se True, una zona con error_flags != 0 (dati mancanti, pixel a bassa
# confidenza, zona oscurata) è considerata NON libera (fail-safe).
ERROR_FLAGS_BLOCK = True

# --- PLC Siemens S7-1500 ---
# False = nessuna comunicazione col PLC (prove del solo sensore, risultati
# visibili nella pagina web e nel log).
PLC_ENABLED = False
PLC_IP = "192.168.1.10"
PLC_RACK = 0
PLC_SLOT = 1
PLC_DB_NUMBER = 7950           # DB di scambio (layout in plc_link.py / README)

# Periodo di scrittura del DB verso il PLC (anche se non arrivano pacchetti,
# il DB viene riscritto per tenere vivo l'heartbeat del PC).
PLC_WRITE_PERIOD_S = 0.02

# Se PLC_Heartbeat (scritto dal PLC) non cambia entro questo tempo, il PC
# segnala PLC_Heartbeat_OK = 0 e lo registra nel log.
PLC_HEARTBEAT_TIMEOUT_S = 1.0

# Attesa tra un tentativo di riconnessione al PLC e il successivo.
PLC_RECONNECT_DELAY_S = 1.0

# --- Pagina web di diagnostica ---
# Stato live di sensore/zone/PLC su http://127.0.0.1:<porta> (solo questo PC).
WEB_UI_ENABLED = True
WEB_UI_PORT = 8080

# --- Varie ---
SENSOR_HTTP_CHECK = True       # all'avvio legge firmware/config/zone live via HTTP (solo diagnostica)
LOG_FILE = "lidar_anticollisione.log"
