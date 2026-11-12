import argparse, json, logging, sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OBLIGATORIAS = ["id_transaccion", "timestamp", "importe_eur", "unidades"]
SECUNDARIAS_CAT = ["categoria", "metodo_pago"]
FORMATO_TS = "%Y-%m-%d %H:%M:%S"
K_IQR = 3.0

log = logging.getLogger("saneamiento")


def configurar_log(ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(ruta, "a", "utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)


class Cuarentena:
    def __init__(self):
        self.partes = []

    def apartar(self, df, mascara, motivo):
        mascara = mascara.fillna(False)
        n = int(mascara.sum())
        if n:
            self.partes.append(df[mascara].assign(motivo=motivo))
            log.warning("ANOMALÍA  %-28s %3d registro(s) -> cuarentena", motivo, n)
        else:
            log.info("OK        %-28s sin incidencias", motivo)
        return df[~mascara]

    def frame(self):
        return pd.concat(self.partes, ignore_index=True) if self.partes else pd.DataFrame()


def cargar(staging):
    ficheros = sorted(Path(staging).glob("*.csv"))
    if not ficheros:
        log.critical("No hay ficheros verificados en %s", staging)
        sys.exit(1)
    partes = []
    for f in ficheros:
        d = pd.read_csv(f, dtype=str)
        d["origen"] = f.name
        d["linea_csv"] = d.index + 2
        log.info("Cargado   %-34s %3d filas", f.name, len(d))
        partes.append(d)
    return pd.concat(partes, ignore_index=True)


def limites_iqr(serie, k=K_IQR):
    q1, q3 = serie.quantile(0.25), serie.quantile(0.75)
    iqr = q3 - q1
    return float(q1 - k * iqr), float(q3 + k * iqr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", default=str(ROOT / "workspace"))
    ap.add_argument("--log", default=str(ROOT / "logs" / "ejecucion_pipeline.log"))
    a = ap.parse_args()
    work = Path(a.work)
    configurar_log(Path(a.log))
    (work / "quarantine").mkdir(parents=True, exist_ok=True)
    (work / "processed").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "output_samples").mkdir(exist_ok=True)

    log.info("=== FASE 2: saneamiento y coherencia ===")
    df = cargar(work / "staging" / "verified")
    columnas = [c for c in df.columns if c not in ("origen", "linea_csv")]
    total = len(df)
    ahora = datetime.now()

    df = df.replace(r"^\s*$", pd.NA, regex=True)
    completitud = float(df[columnas].notna().all(axis=1).mean() * 100)
    ids = df["id_transaccion"].dropna()
    unicidad = float(ids.nunique() / len(ids) * 100)
    completitud_col = {c: round(float(df[c].notna().mean() * 100), 2) for c in columnas}
    nulos_col = {c: int(df[c].isna().sum()) for c in columnas}

    q = Cuarentena()
    for c in OBLIGATORIAS:
        df = q.apartar(df, df[c].isna(), f"{c}_nulo")

    df["importe_eur"] = pd.to_numeric(df["importe_eur"], errors="coerce")
    df["unidades"] = pd.to_numeric(df["unidades"], errors="coerce")
    df["ts"] = pd.to_datetime(df["timestamp"], format=FORMATO_TS, errors="coerce")
    df = q.apartar(df, df["importe_eur"].isna() | df["unidades"].isna(), "valor_numerico_invalido")
    df = q.apartar(df, df["ts"].isna(), "timestamp_corrupto")

    df = q.apartar(df, df["ts"] > ahora, "timestamp_futuro")
    df = q.apartar(df, df["ts"].dt.year < ahora.year, "timestamp_anio_anterior")

    df = q.apartar(df, df["importe_eur"] <= 0, "importe_no_positivo")
    df = q.apartar(df, df["unidades"] < 1, "unidades_menor_que_1")

    df = q.apartar(df, df["id_transaccion"].duplicated(keep="first"), "id_transaccion_duplicado")

    validos_dominio = len(df)
    validez = validos_dominio / total * 100

    log.info("=== Detección de outliers (Tukey, k=%.1f) ===", K_IQR)
    outl, mask_out, limites = [], pd.Series(False, index=df.index), {}
    for col in ["importe_eur", "unidades"]:
        lo, hi = limites_iqr(df[col])
        limites[col] = [round(lo, 2), round(hi, 2)]
        m = (df[col] < lo) | (df[col] > hi)
        log.info("Límites %-12s [%.2f, %.2f] -> %d atípico(s)", col, lo, hi, int(m.sum()))
        if m.any():
            outl.append(df[m & ~mask_out].assign(motivo=f"outlier_{col}"))
        mask_out |= m
    outliers = pd.concat(outl, ignore_index=True) if outl else pd.DataFrame(columns=df.columns.tolist() + ["motivo"])
    if len(outliers):
        log.warning("ANOMALÍA  %-28s %3d registro(s) -> quarantine/outliers.csv", "outliers_estadisticos", len(outliers))
    df = df[~mask_out].copy()

    imputados = {}
    for c in SECUNDARIAS_CAT:
        n = int(df[c].isna().sum())
        if n:
            df[c] = df[c].fillna("DESCONOCIDO")
            imputados[c] = n
            log.info("IMPUTA    %s: %d nulo(s) -> 'DESCONOCIDO'", c, n)

    log.info("=== FASE 3: exportación y perfilado ===")
    df["timestamp"] = df["ts"].dt.strftime(FORMATO_TS)
    final = (df[["id_transaccion", "tienda", "timestamp", "categoria", "importe_eur",
                 "unidades", "metodo_pago", "origen"]]
             .assign(unidades=lambda d: d["unidades"].astype(int))
             .sort_values("timestamp").reset_index(drop=True))
    final.to_csv(work / "processed" / "dataset_kdd_listo.csv", index=False)

    cols_q = columnas + ["origen", "linea_csv", "motivo"]
    bad = q.frame().reindex(columns=cols_q)
    bad.to_csv(work / "quarantine" / "bad_records.csv", index=False)
    outliers.reindex(columns=cols_q).to_csv(work / "quarantine" / "outliers.csv", index=False)

    s = final["importe_eur"]
    dist = {"media": round(float(s.mean()), 2), "mediana": round(float(s.median()), 2),
            "desv_tipica": round(float(s.std()), 2), "p25": round(float(s.quantile(.25)), 2),
            "p75": round(float(s.quantile(.75)), 2), "min": round(float(s.min()), 2),
            "max": round(float(s.max()), 2)}
    motivos = bad["motivo"].value_counts().to_dict()
    if len(outliers):
        motivos.update(outliers["motivo"].value_counts().to_dict())
    ipath = work / "staging" / "integridad.json"
    integ = json.loads(ipath.read_text(encoding="utf-8")) if ipath.exists() else {}

    perfil = {
        "generado": ahora.strftime(FORMATO_TS),
        "registros_brutos_cargados": total,
        "registros_finales": len(final),
        "registros_cuarentena_bad": len(bad),
        "registros_cuarentena_outliers": len(outliers),
        "completitud_pct": round(completitud, 2),
        "completitud_por_columna_pct": completitud_col,
        "nulos_por_columna": nulos_col,
        "unicidad_id_pct": round(unicidad, 2),
        "validez_dominio_pct": round(validez, 2),
        "retencion_final_pct": round(len(final) / total * 100, 2),
        "integridad_criptografica": integ,
        "limites_outliers_iqr": limites,
        "imputaciones": imputados,
        "motivos_rechazo": motivos,
        "distribucion_importe_eur": dist,
        "completitud_dataset_final_pct": round(float(final.notna().all(axis=1).mean() * 100), 2),
    }
    (ROOT / "output_samples" / "metricas_perfilado.json").write_text(
        json.dumps(perfil, indent=2, ensure_ascii=False), encoding="utf-8")
    escribir_manifiesto_cuarentena(integ, bad, outliers, ahora)
    escribir_informe(perfil)
    log.info("Dataset final: %d registros (%.2f%% del bruto) -> processed/dataset_kdd_listo.csv",
             len(final), perfil["retencion_final_pct"])
    log.info("Perfilado ISO/IEC 25012: completitud=%.2f%% unicidad=%.2f%% validez=%.2f%% integridad=%s%%",
             completitud, unicidad, validez, integ.get("tasa_integridad_pct", "n/d"))
    return 0


def escribir_manifiesto_cuarentena(integ, bad, outliers, ahora):
    L = [f"QUARANTINE MANIFEST - lote raw_batch_202611 - {ahora.strftime(FORMATO_TS)}", "=" * 72, "",
         "[A] FICHEROS en quarantine/tampered/ (fallo de integridad SHA-256)"]
    if integ.get("rechazados"):
        for r in integ["rechazados"]:
            L += [f"  - {r['fichero']}", f"      esperado  : {r['esperado']}", f"      calculado : {r['calculado']}",
                  "      motivo    : FIRMA_DISCREPANTE"]
    else:
        L.append("  (ninguno)")
    for n in integ.get("ausentes", []):
        L.append(f"  - {n}  (AUSENTE: listado en manifiesto, no recibido)")
    L += ["", f"[B] REGISTROS en quarantine/bad_records.csv: {len(bad)}",
          f"  {'origen':<32}{'linea':>6}  {'id_transaccion':<20} motivo"]
    for _, r in bad.iterrows():
        L.append(f"  {r['origen']:<32}{int(r['linea_csv']):>6}  {str(r['id_transaccion'] if pd.notna(r['id_transaccion']) else '<NULO>'):<20} {r['motivo']}")
    L += ["", f"[C] OUTLIERS en quarantine/outliers.csv: {len(outliers)}",
          f"  {'origen':<32}{'linea':>6}  {'id_transaccion':<20} motivo (valor)"]
    for _, r in outliers.iterrows():
        col = r["motivo"].replace("outlier_", "")
        L.append(f"  {r['origen']:<32}{int(r['linea_csv']):>6}  {str(r['id_transaccion']):<20} {r['motivo']} ({r[col]})")
    (ROOT / "output_samples" / "quarantine_manifest.txt").write_text("\n".join(L) + "\n", encoding="utf-8")


def escribir_informe(p):
    i = p.get("integridad_criptografica", {})
    filas_motivo = "\n".join(f"| `{k}` | {v} |" for k, v in sorted(p["motivos_rechazo"].items(), key=lambda x: -x[1]))
    rech = ", ".join(f"`{r['fichero']}`" for r in i.get("rechazados", [])) or "ninguno"
    val = ", ".join(f"`{v}`" for v in i.get("validados", [])) or "ninguno"
    d, lim = p["distribucion_importe_eur"], p["limites_outliers_iqr"]
    imp = "; ".join(f"`{c}`: {n} nulo(s) → `DESCONOCIDO`" for c, n in p["imputaciones"].items()) or "ninguna"
    md = f"""# INFORME DE CALIDAD KDD — Lote raw_batch_202611

Generado: {p['generado']} · Marco de referencia: ISO/IEC 25012

## 1. Resumen ejecutivo

| Indicador | Valor |
|---|---|
| Ficheros en manifiesto | {i.get('total_manifiesto', 'n/d')} |
| Ficheros validados (SHA-256) | {len(i.get('validados', []))} |
| Ficheros rechazados / ausentes | {len(i.get('rechazados', []))} / {len(i.get('ausentes', []))} |
| Registros brutos cargados (solo ficheros verificados) | {p['registros_brutos_cargados']} |
| Registros en dataset final | {p['registros_finales']} ({p['retencion_final_pct']} %) |
| Registros en `quarantine/bad_records.csv` | {p['registros_cuarentena_bad']} |
| Registros en `quarantine/outliers.csv` | {p['registros_cuarentena_outliers']} |

## 2. Métricas ISO/IEC 25012 (sobre el dato bruto de los ficheros verificados)

| Característica | Definición aplicada | Resultado |
|---|---|---|
| **Completitud** | registros sin nulos / registros totales × 100 | **{p['completitud_pct']} %** |
| **Unicidad** (clave) | ids distintos / ids no nulos × 100 | **{p['unicidad_id_pct']} %** |
| **Validez** (formato y dominio) | registros que superan obligatorios, tipos, fechas y reglas de dominio / totales × 100 | **{p['validez_dominio_pct']} %** |
| **Integridad criptográfica** | validados / (validados + rechazados + ausentes) × 100 | **{i.get('tasa_integridad_pct', 'n/d')} %** |

Nulos por columna: {json.dumps(p['nulos_por_columna'], ensure_ascii=False)}

Completitud del dataset final tras el saneamiento: **{p['completitud_dataset_final_pct']} %**.

## 3. Integridad criptográfica (Fase 1)

- Validados → `staging/verified/`: {val}
- Rechazados → `quarantine/tampered/`: {rech}

Superar el hash solo certifica que el fichero no cambió en tránsito; por eso los ficheros válidos pasan igualmente por las comprobaciones de las fases 2 y 3. El fichero rechazado **no se analiza ni se incorpora** al dataset: su contenido no es fiable hasta que el origen lo reenvíe.

## 4. Registros segregados por motivo

| Motivo | Registros |
|---|---|
{filas_motivo}

Detalle registro a registro (fichero, línea, id) en `output_samples/quarantine_manifest.txt`.
Límites de outliers (IQR extremo, k = 3): `importe_eur` {lim['importe_eur']}, `unidades` {lim['unidades']}.

## 5. Distribución de `importe_eur` tras la limpieza

| Media | Mediana | Desv. típica | P25 | P75 | Mín | Máx |
|---|---|---|---|---|---|---|
| {d['media']} | {d['mediana']} | {d['desv_tipica']} | {d['p25']} | {d['p75']} | {d['min']} | {d['max']} |

## 6. Conclusiones técnicas

**Decisiones de filtrado (a cuarentena, sin imputar):**

- `id_transaccion` nulo: sin clave no se puede trazar ni deduplicar la transacción; imputarla inventaría una identidad.
- `timestamp`, `importe_eur` o `unidades` nulos o no numéricos: son variables obligatorias; un valor imputado sesgaría directamente ingresos y volumen.
- Timestamps corruptos, futuros o de año anterior al en curso: rompen la coherencia temporal del lote.
- `importe_eur` ≤ 0 y `unidades` < 1: violan reglas de dominio (una venta debe ser positiva).
- `id_transaccion` duplicado: se conserva la primera aparición para garantizar unicidad y evitar doble contabilización.
- Outliers (Tukey, k = 3): se aíslan solo los atípicos extremos. Un umbral conservador evita eliminar compras legítimas altas; se envían a `quarantine/outliers.csv` para revisión en lugar de borrarse.

**Decisiones de imputación (variables secundarias):** {imp}. Se usa una etiqueta explícita en lugar de la moda para no inventar un valor concreto ni sesgar frecuencias; así se conserva el registro (sus campos críticos son válidos) y es filtrable en el análisis. Para variables numéricas secundarias se usaría la mediana (robusta a extremos), aunque este lote no tiene ninguna con nulos.

**Por qué el dataset resultante es consistente para minería de datos (KDD):**

- Todas las firmas SHA-256 de los ficheros usados coinciden con el manifiesto de origen.
- Clave única, sin nulos ({p['completitud_dataset_final_pct']} % de completitud), tipos correctos, fechas en ISO 8601 y dentro del año en curso.
- Cumple las reglas de dominio y no contiene valores extremos que distorsionarían medias, varianzas ni modelos sensibles a la escala (regresión, clustering por distancia).
- Cada registro descartado queda trazado con su motivo, fichero y línea, lo que permite auditoría y reprocesado.

**Limitaciones:** el rechazo completo de un fichero reduce la cobertura del lote (hay que reclamar el reenvío al origen) y el umbral de outliers debe validarse con el negocio.
"""
    (ROOT / "reports" / "INFORME_CALIDAD_KDD.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())