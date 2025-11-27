# Bazarr Integration Guide voor SRTGEN

## Overzicht
SRTGEN kan automatisch ondertitels genereren via Bazarr's post-processing wanneer Bazarr geen ondertitels online kan vinden.

## Setup Stappen

### 1. Kopieer het Script naar Bazarr
```bash
# Maak custom scripts directory in Bazarr
mkdir -p /mnt/user/appdata/bazarr/custom_scripts

# Kopieer het post-processing script
cp /mnt/user/appdata/SRTGEN/bazarr_postprocess.sh /mnt/user/appdata/bazarr/custom_scripts/
chmod +x /mnt/user/appdata/bazarr/custom_scripts/bazarr_postprocess.sh
```

### 2. Configureer Bazarr
1. Open Bazarr WebUI
2. Ga naar **Settings** → **General** → **Post-Processing**
3. Enable **Use Custom Post-Processing Script**
4. Script pad: `/custom_scripts/bazarr_postprocess.sh`

### 3. Configureer Environment Variables (Optioneel)
Voeg deze toe aan je Bazarr docker container:

```bash
docker run -d \
  --name bazarr \
  -e SRTGEN_TARGET_LANG=nl \      # Doeltaal (nl, en, de, fr, etc.)
  -e SRTGEN_MODEL=base \           # Whisper model (tiny, base, small, medium, large)
  -e SRTGEN_OVERWRITE=false \      # Overschrijf bestaande SRT files
  ...
```

Of in Unraid Docker template:
```
SRTGEN_TARGET_LANG=nl
SRTGEN_MODEL=base
SRTGEN_OVERWRITE=false
```

### 4. Zorg voor Volume Toegang
Beide containers moeten toegang hebben tot dezelfde video bestanden:

**SRTGEN volumes:**
```yaml
volumes:
  - /mnt/user/media:/input
  - /mnt/user/appdata/srtgen/output:/output
```

**Bazarr volumes:**
```yaml
volumes:
  - /mnt/user/media:/media
```

### 5. Test de Integratie
1. Laat Bazarr zoeken naar ontbrekende ondertitels
2. Check de logs in `/tmp/srtgen-bazarr/` (binnen SRTGEN container)
3. Of check Bazarr's logs voor post-processing output

## Workflow

```
Bazarr zoekt ondertitels online
         ↓
   Niet gevonden?
         ↓
Post-processing script triggered
         ↓
SRTGEN genereert ondertitels lokaal
         ↓
SRT bestand beschikbaar in video directory
         ↓
Bazarr detecteert nieuwe ondertitels
```

## Script Configuratie

### Environment Variables
- `SRTGEN_TARGET_LANG`: Doeltaal code (default: `nl`)
- `SRTGEN_MODEL`: Whisper model (default: `base`)
- `SRTGEN_OVERWRITE`: Overschrijf bestaande SRT (default: `false`)
- `SRTGEN_CONTAINER`: Docker container naam (default: `srtgen`)

### Supported Formats
- Alleen **MKV** bestanden worden verwerkt
- Andere formats worden overgeslagen met waarschuwing

### Logs
Logs worden opgeslagen in:
- Container path: `/tmp/srtgen-bazarr/bazarr_YYYYMMDD_HHMMSS.log`
- Host path: Check met `docker exec srtgen ls -la /tmp/srtgen-bazarr/`

## Troubleshooting

### Script draait niet
```bash
# Check of script executable is
docker exec bazarr ls -la /custom_scripts/bazarr_postprocess.sh

# Test handmatig
docker exec bazarr /bin/bash /custom_scripts/bazarr_postprocess.sh
```

### Container niet gevonden
```bash
# Check SRTGEN container naam
docker ps | grep srtgen

# Update in script als naam anders is
SRTGEN_CONTAINER="jouw_container_naam"
```

### Video pad niet gevonden
Zorg dat beide containers dezelfde mount paths gebruiken:
```bash
# Check volume mounts
docker inspect srtgen | grep Mounts -A 20
docker inspect bazarr | grep Mounts -A 20
```

### Logs bekijken
```bash
# SRTGEN logs
docker logs srtgen

# Bazarr post-processing logs
docker exec srtgen cat /tmp/srtgen-bazarr/bazarr_*.log

# Of alle logs
docker exec srtgen tail -f /tmp/srtgen-bazarr/*.log
```

## Advanced: Selectieve Processing

Om alleen bepaalde shows/movies te transcriberen, pas het script aan:

```bash
# Voeg toe na "log 'Video file: $VIDEO_FILE'"

# Alleen voor specifieke series
if [[ "$VIDEO_FILE" =~ "Breaking Bad" ]]; then
    TARGET_LANGUAGE="en"
elif [[ "$VIDEO_FILE" =~ "Dark" ]]; then
    TARGET_LANGUAGE="de"
else
    log "Skipping transcription for this file"
    exit 0
fi
```

## Performance Tips

1. **Model selectie**: 
   - `tiny`: Snelst, minste nauwkeurig
   - `base`: Goede balans (aanbevolen)
   - `medium`: Beter, maar trager
   - `large`: Beste kwaliteit, veel trager

2. **Concurrent jobs**: 
   Stel in via SRTGEN WebUI Settings → Max Concurrent Jobs

3. **GPU gebruik**: 
   SRTGEN gebruikt automatisch CUDA als beschikbaar

## Voorbeeld Setup (Unraid)

```yaml
# SRTGEN Container
Container: srtgen
Repository: agoddrie/srtgen:latest
Network: bridge
Port: 5000 → 5000
Volume: /mnt/user/media → /input
Volume: /mnt/user/appdata/srtgen/output → /output
GPU: Yes (NVIDIA)

# Bazarr Container  
Container: bazarr
Repository: linuxserver/bazarr:latest
Network: bridge
Port: 6767 → 6767
Volume: /mnt/user/media → /media
Volume: /mnt/user/appdata/bazarr → /config
Volume: /mnt/user/appdata/bazarr/custom_scripts → /custom_scripts
Extra: SRTGEN_TARGET_LANG=nl
Extra: SRTGEN_MODEL=base
```

## Vragen?
Check de logs, of open een issue op GitHub: https://github.com/AGo444/srtgen
