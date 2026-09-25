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
  work_code?: string
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
  verified_pieces: number
  quality: string
  piece_segments: [number, number, number][]
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
  resolution?: string
  cover_url?: string
  cover_thumb_url?: string
  cover_mid_url?: string
  cover_w?: number
  cover_h?: number
  magnet?: string
  magnet_status?: string
  number?: number
  user_liked?: boolean
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
  number?: number
  titles: Title[]
  posts: Post[]
}

export interface SearchResultStar {
  name: string
  url: string
  followed: boolean
}

export interface SearchResultItem {
  code: string
  title?: string
  release_date?: string
  views?: number
  likes?: number
  cover_url?: string
  resolution?: string
  size?: string
  seeds?: number
  in_library: boolean
  source?: 'ijav' | 'sukebei'
  stars: SearchResultStar[]
}

export interface SearchResponse {
  query: string
  count: number
  items: SearchResultItem[]
}
