# INFORME DE CALIDAD KDD — Lote raw_batch_202611

Generado: 2026-11-12 20:37:49 · Marco de referencia: ISO/IEC 25012

## 1. Resumen ejecutivo

| Indicador | Valor |
|---|---|
| Ficheros en manifiesto | 3 |
| Ficheros validados (SHA-256) | 2 |
| Ficheros rechazados / ausentes | 1 / 0 |
| Registros brutos cargados (solo ficheros verificados) | 100 |
| Registros en dataset final | 87 (87.0 %) |
| Registros en `quarantine/bad_records.csv` | 10 |
| Registros en `quarantine/outliers.csv` | 3 |

## 2. Métricas ISO/IEC 25012 (sobre el dato bruto de los ficheros verificados)

| Característica | Definición aplicada | Resultado |
|---|---|---|
| **Completitud** | registros sin nulos / registros totales × 100 | **95.0 %** |
| **Unicidad** (clave) | ids distintos / ids no nulos × 100 | **100.0 %** |
| **Validez** (formato y dominio) | registros que superan obligatorios, tipos, fechas y reglas de dominio / totales × 100 | **90.0 %** |
| **Integridad criptográfica** | validados / (validados + rechazados + ausentes) × 100 | **66.67 %** |

Nulos por columna: {"id_transaccion": 2, "tienda": 0, "timestamp": 0, "categoria": 1, "importe_eur": 1, "unidades": 0, "metodo_pago": 1}

Completitud del dataset final tras el saneamiento: **100.0 %**.

## 3. Integridad criptográfica (Fase 1)

- Validados → `staging/verified/`: `transacciones_tienda_norte.csv`, `transacciones_tienda_este.csv`
- Rechazados → `quarantine/tampered/`: `transacciones_tienda_sur.csv`

Superar el hash solo certifica que el fichero no cambió en tránsito; por eso los ficheros válidos pasan igualmente por las comprobaciones de las fases 2 y 3. El fichero rechazado **no se analiza ni se incorpora** al dataset: su contenido no es fiable hasta que el origen lo reenvíe.

## 4. Registros segregados por motivo

| Motivo | Registros |
|---|---|
| `outlier_importe_eur` | 3 |
| `id_transaccion_nulo` | 2 |
| `importe_no_positivo` | 2 |
| `unidades_menor_que_1` | 2 |
| `importe_eur_nulo` | 1 |
| `timestamp_corrupto` | 1 |
| `timestamp_futuro` | 1 |
| `timestamp_anio_anterior` | 1 |

Detalle registro a registro (fichero, línea, id) en `output_samples/quarantine_manifest.txt`.
Límites de outliers (IQR extremo, k = 3): `importe_eur` [-44.85, 188.65], `unidades` [-7.0, 14.0].

## 5. Distribución de `importe_eur` tras la limpieza

| Media | Mediana | Desv. típica | P25 | P75 | Mín | Máx |
|---|---|---|---|---|---|---|
| 70.99 | 73.44 | 26.34 | 53.8 | 85.34 | 12.5 | 124.47 |

## 6. Conclusiones técnicas

**Decisiones de filtrado (a cuarentena, sin imputar):**

- `id_transaccion` nulo: sin clave no se puede trazar ni deduplicar la transacción; imputarla inventaría una identidad.
- `timestamp`, `importe_eur` o `unidades` nulos o no numéricos: son variables obligatorias; un valor imputado sesgaría directamente ingresos y volumen.
- Timestamps corruptos, futuros o de año anterior al en curso: rompen la coherencia temporal del lote.
- `importe_eur` ≤ 0 y `unidades` < 1: violan reglas de dominio (una venta debe ser positiva).
- `id_transaccion` duplicado: se conserva la primera aparición para garantizar unicidad y evitar doble contabilización.
- Outliers (Tukey, k = 3): se aíslan solo los atípicos extremos. Un umbral conservador evita eliminar compras legítimas altas; se envían a `quarantine/outliers.csv` para revisión en lugar de borrarse.

**Decisiones de imputación (variables secundarias):** `categoria`: 1 nulo(s) → `DESCONOCIDO`; `metodo_pago`: 1 nulo(s) → `DESCONOCIDO`. Se usa una etiqueta explícita en lugar de la moda para no inventar un valor concreto ni sesgar frecuencias; así se conserva el registro (sus campos críticos son válidos) y es filtrable en el análisis. Para variables numéricas secundarias se usaría la mediana (robusta a extremos), aunque este lote no tiene ninguna con nulos.

**Por qué el dataset resultante es consistente para minería de datos (KDD):**

- Todas las firmas SHA-256 de los ficheros usados coinciden con el manifiesto de origen.
- Clave única, sin nulos (100.0 % de completitud), tipos correctos, fechas en ISO 8601 y dentro del año en curso.
- Cumple las reglas de dominio y no contiene valores extremos que distorsionarían medias, varianzas ni modelos sensibles a la escala (regresión, clustering por distancia).
- Cada registro descartado queda trazado con su motivo, fichero y línea, lo que permite auditoría y reprocesado.

**Limitaciones:** el rechazo completo de un fichero reduce la cobertura del lote (hay que reclamar el reenvío al origen) y el umbral de outliers debe validarse con el negocio.
