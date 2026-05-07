import sharp from 'sharp'
import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const svg = readFileSync(join(__dirname, '../public/icon.svg'))

await sharp(svg)
  .resize(192, 192)
  .png()
  .toFile(join(__dirname, '../public/icon-192x192.png'))

await sharp(svg)
  .resize(512, 512)
  .png()
  .toFile(join(__dirname, '../public/icon-512x512.png'))

console.log('Icons generated')
