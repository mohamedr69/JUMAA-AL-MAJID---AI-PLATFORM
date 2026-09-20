/** Floor numbers as short ranges: [2,3,4,5,12,13] -> "2-5, 12-13". */
export function formatFloors(fs: number[]): string {
  const out: string[] = []
  let start = fs[0]
  let prev = fs[0]
  for (const n of [...fs.slice(1), NaN]) {
    if (n === prev + 1) {
      prev = n
      continue
    }
    out.push(start === prev ? `${start}` : `${start}-${prev}`)
    start = prev = n
  }
  return out.join(', ')
}
