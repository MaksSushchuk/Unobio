// Geometry for the pipeline diagram, computed from the config and the
// container width. Horizontal (stages as columns) on wide screens, vertical
// (stages as rows, parallel agents in a 2-col grid) on narrow ones.

import type { PipelineStage } from '../../pipeline.config'

export const NODE_H = 56
const ROW = NODE_H + 12
const CHIP_H = 26
const CHIP_ROW = CHIP_H + 4
const VERTICAL_BELOW = 640

export interface NodeBox {
  id: string
  /** Center. */
  x: number
  y: number
  w: number
}

export interface ChipBox {
  source_id: string
  /** Top-left. */
  x: number
  y: number
  w: number
}

export interface EdgePath {
  from: string
  to: string
  d: string
}

export interface LoopPath {
  from_stage: string
  to_stage: string
  label: string
  d: string
  labelAt: { x: number; y: number }
  arrowAt: { x: number; y: number }
  arrowDir: 'down' | 'left'
}

export interface Layout {
  vertical: boolean
  height: number
  nodes: NodeBox[]
  chips: ChipBox[]
  edges: EdgePath[]
  loops: LoopPath[]
}

export function computeLayout(pipeline: PipelineStage[], width: number): Layout {
  return width < VERTICAL_BELOW ? vertical(pipeline, width) : horizontal(pipeline, width)
}

function horizontal(pipeline: PipelineStage[], width: number): Layout {
  const hasLoop = pipeline.some((s) => s.follow_up)
  const top = hasLoop ? 60 : 8
  const colW = width / pipeline.length
  const w = Math.min(184, colW - 28)

  // Every stage's agents are centered on one middle line; a chip list hangs
  // below its node, so it only adds room below that line.
  const above = Math.max(...pipeline.map((s) => (s.agents.length * ROW - 12) / 2))
  const below = Math.max(
    ...pipeline.map((s) =>
      Math.max((s.agents.length * ROW - 12) / 2, s.sources ? NODE_H / 2 + 10 + s.sources.length * CHIP_ROW - 4 : 0),
    ),
  )
  const mid = top + above

  const nodes: NodeBox[] = []
  const chips: ChipBox[] = []
  const byStage: NodeBox[][] = pipeline.map((stage, i) => {
    const x = colW * (i + 0.5)
    const col = stage.agents.map((a, k) => ({ id: a.id, x, y: mid + (k - (stage.agents.length - 1) / 2) * ROW, w }))
    stage.sources?.forEach((src, j) => {
      chips.push({ source_id: src.id, x: x - w / 2, y: col[0].y + NODE_H / 2 + 10 + j * CHIP_ROW, w })
    })
    nodes.push(...col)
    return col
  })

  const edges: EdgePath[] = []
  for (let i = 0; i < byStage.length - 1; i++) {
    for (const a of byStage[i]) {
      for (const b of byStage[i + 1]) {
        const x1 = a.x + a.w / 2
        const x2 = b.x - b.w / 2
        const mx = (x1 + x2) / 2
        edges.push({ from: a.id, to: b.id, d: `M ${x1} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${x2} ${b.y}` })
      }
    }
  }

  const loops: LoopPath[] = []
  pipeline.forEach((stage, i) => {
    if (!stage.follow_up) return
    const ti = pipeline.findIndex((s) => s.id === stage.follow_up!.target_stage)
    const s = byStage[i][0]
    const t = byStage[ti][0]
    const y1 = s.y - NODE_H / 2
    const y2 = t.y - NODE_H / 2 - 3
    const c = 4
    loops.push({
      from_stage: stage.id,
      to_stage: pipeline[ti].id,
      label: stage.follow_up.label,
      d: `M ${s.x} ${y1} C ${s.x} ${c}, ${t.x} ${c}, ${t.x} ${y2}`,
      labelAt: { x: (s.x + t.x) / 2, y: 0.125 * y1 + 0.75 * c + 0.125 * y2 },
      arrowAt: { x: t.x, y: y2 },
      arrowDir: 'down',
    })
  })

  return { vertical: false, height: mid + below + 4, nodes, chips, edges, loops }
}

function vertical(pipeline: PipelineStage[], width: number): Layout {
  const hasLoop = pipeline.some((s) => s.follow_up)
  const inner = width - (hasLoop ? 40 : 0)
  const gap = 12

  const nodes: NodeBox[] = []
  const chips: ChipBox[] = []
  const byStage: NodeBox[][] = []
  const stageBottom: number[] = []
  let y = 4

  for (const stage of pipeline) {
    const parallel = stage.agents.length > 1
    const w = parallel ? (inner - gap) / 2 : Math.min(260, inner)
    const col = stage.agents.map((a, k) => {
      const c = parallel ? k % 2 : 0
      const r = parallel ? Math.floor(k / 2) : 0
      const x = parallel ? c * (w + gap) + w / 2 : inner / 2
      return { id: a.id, x, y: y + r * ROW + NODE_H / 2, w }
    })
    const rows = parallel ? Math.ceil(stage.agents.length / 2) : 1
    y += rows * ROW - 12

    if (stage.sources) {
      const cw = (inner - 8) / 2
      stage.sources.forEach((src, j) => {
        chips.push({ source_id: src.id, x: (j % 2) * (cw + 8), y: y + 10 + Math.floor(j / 2) * CHIP_ROW, w: cw })
      })
      y += 10 + Math.ceil(stage.sources.length / 2) * CHIP_ROW - 4
    }

    nodes.push(...col)
    byStage.push(col)
    stageBottom.push(y)
    y += 40
  }

  const edges: EdgePath[] = []
  for (let i = 0; i < byStage.length - 1; i++) {
    for (const a of byStage[i]) {
      for (const b of byStage[i + 1]) {
        const y1 = stageBottom[i]
        const y2 = b.y - NODE_H / 2
        const my = (y1 + y2) / 2
        edges.push({ from: a.id, to: b.id, d: `M ${a.x} ${y1} C ${a.x} ${my}, ${b.x} ${my}, ${b.x} ${y2}` })
      }
    }
  }

  const loops: LoopPath[] = []
  pipeline.forEach((stage, i) => {
    if (!stage.follow_up) return
    const ti = pipeline.findIndex((s) => s.id === stage.follow_up!.target_stage)
    const s = byStage[i][0]
    const t = byStage[ti][0]
    const x1 = s.x + s.w / 2
    const x2 = t.x + t.w / 2 + 3
    const c = width - 2
    loops.push({
      from_stage: stage.id,
      to_stage: pipeline[ti].id,
      label: stage.follow_up.label,
      d: `M ${x1} ${s.y} C ${c} ${s.y}, ${c} ${t.y}, ${x2} ${t.y}`,
      labelAt: { x: 0.125 * x1 + 0.75 * c + 0.125 * x2, y: (s.y + t.y) / 2 },
      arrowAt: { x: x2, y: t.y },
      arrowDir: 'left',
    })
  })

  return { vertical: true, height: y - 40 + 4, nodes, chips, edges, loops }
}
