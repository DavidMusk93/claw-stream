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

export interface Post {
  platform: string
  content: string
  url: string
  posted_at: string
}

export interface Star {
  name: string
  jp?: string
  handle?: string
  code: string
  type?: string
  note?: string
  titles: Title[]
  posts: Post[]
}
