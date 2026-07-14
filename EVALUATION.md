# EVALUATION — cómo medir si un render "pasa por real" (best practices)

El valor de esta skill no es "OSM → Blender" (eso ya existe), sino el **loop**:
`lugar → escena → render → comparación con la realidad → corrección → repetir`.
Este documento define el método de evaluación con base en best practices de
evaluación perceptual de imágenes y de LLM/VLM-as-judge.

## Principios
1. **Forced-choice, no un 0–100 suelto.** "¿Pasa por foto real?" se operacionaliza
   como **2AFC**: al juez se le muestran el render y un recorte real *pose-matcheado*
   y se le fuerza "¿cuál es la foto real?". La métrica primaria es la **precisión del
   discriminador** (50% = indistinguible; >75% = claramente CG). El score 0–100 se
   conserva solo como señal secundaria/diagnóstica, porque es inestable entre jueces.
2. **Controlar confounds ANTES de comparar.** El juez no debe penalizar diferencias
   de encuadre/hora/POV como si fueran falta de realismo. Hay que **matchear la cámara**
   a la referencia (FOV, heading, pitch, fecha/hora de captura del pano de Street View).
3. **Referencia congelada y held-out.** Capturar varias vistas reales (headings/horas),
   partir en **tune** (para elegir y validar el fix del defecto #1) y **eval held-out**
   (para medir sin sobreajustar al set que estás optimizando).
4. **Panel diverso y anclado.** 4 lentes (materiales / geometría / luz+atmósfera /
   gestalt), cada una instanciada en **≥2 familias de modelos** distintas; el modelo que
   generó/eligió el fix **no** debe ser el único que lo evalúa (evita self-enhancement).
5. **Salida del juez restringida a form-filling con evidencia primero.** Cada lente
   emite JSON fijo: `{observations, giveaways:[{desc, severity 1–5, region_bbox, fix}],
   verdict}` — evidencia antes del veredicto (reduce alucinación/inflación).
6. **Debiasear cada comparación pairwise.** Randomizar cuál imagen es "A", correr **las
   dos órdenes** y promediar; un flip de orden = empate/descartar (el sesgo de posición
   mueve 10–15 pts). Correr cada lente **k≥3** veces a temperatura 0 y tomar mediana.
7. **Métricas automáticas independientes** sobre el set matcheado (no reemplazan al
   panel, lo anclan): **CMMD (CLIP-MMD)** como métrica de distribución (mejor que FID
   para pocas muestras), **CLIP-IQA / NIQE** (calidad no-referencia), **LPIPS** contra la
   referencia matcheada, y opcional un detector real-vs-sintético.
8. **Agregación por defecto, ponderada por severidad y consenso cross-familia.** El
   sintetizador rankea cada defecto por `consenso_entre_familias × severidad_media`, no
   por "apareció una vez". Se arregla el top-1, se re-mide.
9. **Contrato del juez fijado y calibración por corrida.** Persistir en `report.json`:
   `{judge_model_ids, rubric_version, prompt_template_hash, temperature:0}` + un **anchor
   set** de 3–5 imágenes reales conocidas para chequear que el juez las llama "reales"
   (si falla el ancla, la corrida no es confiable).
10. **Human-in-the-loop periódico.** Cada tanto, un 2AFC con humanos sobre el held-out
    siguiendo higiene tipo ITU-R BT.500 (varios observadores, exposición controlada) para
    recalibrar el juez automático.
11. **Voto pairwise de stop/continue.** Tras cada fix: mostrar `render_N` vs `render_{N-1}`
    contra la foto real y preguntar "¿cuál está más cerca de la real?" (swap-averaged). Si
    N no gana, revertir el fix.
12. **Regla de corte pre-registrada multi-métrica.** Dashboard por iteración en
    `report.json`: precisión del discriminador held-out + Wilson CI, CMMD, P(sintético),
    CLIP-IQA y la lista deduplicada de defectos. Cortar cuando: (a) la precisión del
    discriminador entra en el intervalo de indistinguibilidad, o (b) 2 iteraciones sin
    mejora significativa (plateau), o (c) presupuesto agotado.

## Implementación en esta skill (estado)
- **Ya está:** panel adversarial de subagentes (materiales/geometría/luz/gestalt) +
  síntesis con score y defectos priorizados; referencia real de Street View
  (`place_to_3d.py` baja `streetview/heading_*.jpg`).
- **Roadmap (de este doc):** pose-match cámara↔pano; set held-out; panel multi-familia
  con doble-orden y k≥3; `scripts/eval_metrics.py` (CMMD/CLIP-IQA/LPIPS); anchor-set de
  calibración; regla de corte en `report.json`.

## Nota honesta
"Indistinguible / 100" es **asintótico**: un panel adversarial siempre encontrará algún
tell (fotogrametría "melt" en 3D Tiles, materiales inferidos en OSM). El objetivo
operativo real es **precisión del discriminador cercana a 50% en held-out**, no un 100
literal de un juez estricto. El modo Google 3D Tiles es lo más cerca porque es
fotogrametría real.
