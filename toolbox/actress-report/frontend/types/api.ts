export interface StreamCheckResponse {
  hash: string
  cached: boolean
  head_ready: boolean
  path: string
  size: number
  mime: string
}

export interface TorrentStatus {
  hash: string
  name?: string
  ready: boolean
  cached: boolean
  head_ready: boolean
  peers: number
  progress: number
  download_rate: number
  upload_rate: number
  video_file?: string
  video_size: number
  local_size: number
  mime: string
  state: string
}

export interface CacheMetrics {
  total: number
  completed: number
  downloading: number
  used_bytes: number
  used_human: string
  max_bytes: number
  max_human: string
}

export interface Star {
  id?: number
  name: string
  jp_name?: string
  handle?: string
  code?: string
  type: string
  note?: string
}

export interface Title {
  code: string
  title?: string
  date?: string
  views?: string
  likes?: string
  resolution?: string
  cover_url?: string
  magnet?: string
}
