import argparse, hashlib, json, logging, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOQUE = 65536


def calcular_sha256(ruta_fichero):
    hasher = hashlib.sha256()
    with open(ruta_fichero, "rb") as f:
        while chunk := f.read(BLOQUE):
            hasher.update(chunk)
    return hasher.hexdigest()


def configurar_log(ruta_log):
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("integridad")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(ruta_log, "w", "utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log


def leer_manifiesto(ruta, log):
    entradas = []
    with open(ruta, encoding="utf-8") as f:
        for n, linea in enumerate(f, 1):
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            partes = linea.split(maxsplit=1)
            if len(partes) != 2 or len(partes[0]) != 64:
                log.warning("Línea %d del manifiesto malformada: %r", n, linea)
                continue
            entradas.append((partes[0].lower(), partes[1].lstrip("*").strip()))
    return entradas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=str(ROOT / "workspace"), help="directorio de trabajo (contiene raw_batch_202611/)")
    ap.add_argument("--log", default=str(ROOT / "logs" / "ejecucion_pipeline.log"))
    a = ap.parse_args()

    work = Path(a.work)
    raw = work / "raw_batch_202611"
    manifest = raw / "manifest_checksums.sha256"
    verified, tampered = work / "staging" / "verified", work / "quarantine" / "tampered"
    verified.mkdir(parents=True, exist_ok=True)
    tampered.mkdir(parents=True, exist_ok=True)
    log = configurar_log(Path(a.log))

    if not manifest.exists():
        log.critical("Manifiesto no encontrado: %s", manifest)
        return 2

    log.info("=== FASE 1: verificación SHA-256 del lote raw_batch_202611 ===")
    ok, ko, ausentes = [], [], []
    for esperado, nombre in leer_manifiesto(manifest, log):
        ruta = raw / nombre
        if not ruta.exists():
            log.error("AUSENTE   %s (listado en el manifiesto; descarga incompleta?)", nombre)
            ausentes.append(nombre)
            continue
        try:
            calculado = calcular_sha256(ruta)
        except OSError as e:
            log.error("ILEGIBLE  %s: %s", nombre, e)
            shutil.move(str(ruta), tampered / nombre)
            ko.append({"fichero": nombre, "esperado": esperado, "calculado": None})
            continue
        if calculado == esperado:
            shutil.move(str(ruta), verified / nombre)
            log.info("OK        %s sha256=%s... -> staging/verified/", nombre, calculado[:16])
            ok.append(nombre)
        else:
            shutil.move(str(ruta), tampered / nombre)
            log.warning("ALERTA    FIRMA DISCREPANTE en %s", nombre)
            log.warning("          esperado  = %s", esperado)
            log.warning("          calculado = %s", calculado)
            log.warning("          -> movido a quarantine/tampered/")
            ko.append({"fichero": nombre, "esperado": esperado, "calculado": calculado})

    total = len(ok) + len(ko) + len(ausentes)
    tasa = round(100 * len(ok) / total, 2) if total else 0.0
    resumen = {"total_manifiesto": total, "validados": ok, "rechazados": ko,
               "ausentes": ausentes, "tasa_integridad_pct": tasa}
    (work / "staging" / "integridad.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("RESUMEN FASE 1: %d validados, %d rechazados, %d ausentes -> integridad %.2f%%",
             len(ok), len(ko), len(ausentes), tasa)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())