import { createWriteStream, existsSync, mkdirSync, statSync } from "node:fs";
import { pipeline } from "node:stream/promises";
import path from "node:path";

const FEED_URL = process.env.FEED_URL ?? "https://feeds.simplecast.com/EaEV0pvl";

// Change this to "." if you literally want files dumped in the repo root.
const OUTPUT_DIR = process.env.OUTPUT_DIR ?? path.resolve(process.cwd(), "episodes");

type Episode = {
    title: string;
    audioUrl: string;
    extension: string;
};

function decodeXml(value: string): string {
    return value
        .replaceAll("&amp;", "&")
        .replaceAll("&quot;", "\"")
        .replaceAll("&apos;", "'")
        .replaceAll("&lt;", "<")
        .replaceAll("&gt;", ">");
}

function getTagValue(xml: string, tagName: string): string | null {
    const match = xml.match(new RegExp(`<${tagName}[^>]*>([\\s\\S]*?)<\\/${tagName}>`, "i"));
    if (!match) return null;

    return decodeXml(
        match[1]
            .replace(/^<!\[CDATA\[/, "")
            .replace(/\]\]>$/, "")
            .trim(),
    );
}

function getAttribute(tag: string, attrName: string): string | null {
    const match = tag.match(new RegExp(`${attrName}\\s*=\\s*["']([^"']+)["']`, "i"));
    return match ? decodeXml(match[1]) : null;
}

function slugify(value: string): string {
    return value
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-zA-Z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .toLowerCase()
        .slice(0, 90);
}

function getExtension(audioUrl: string): string {
    const parsed = new URL(audioUrl);
    const ext = path.extname(parsed.pathname).replace(".", "").toLowerCase();

    if (ext.length >= 2 && ext.length <= 5) {
        return ext;
    }

    return "mp3";
}

function parseEpisodes(feedXml: string): Episode[] {
    const itemMatches = feedXml.match(/<item\b[\s\S]*?<\/item>/gi) ?? [];

    return itemMatches
        .map((item, index): Episode | null => {
            const enclosure = item.match(/<enclosure\b[^>]*>/i)?.[0];
            const audioUrl = enclosure ? getAttribute(enclosure, "url") : null;

            if (!audioUrl) return null;

            const title = getTagValue(item, "title") ?? `episode-${index + 1}`;

            return {
                title,
                audioUrl,
                extension: getExtension(audioUrl),
            };
        })
        .filter((episode): episode is Episode => episode !== null);
}

async function downloadEpisode(episode: Episode, index: number, total: number): Promise<void> {
    const safeTitle = slugify(episode.title) || `episode-${index + 1}`;
    const filename = `${String(index + 1).padStart(3, "0")}-${safeTitle}.${episode.extension}`;
    const outputPath = path.join(OUTPUT_DIR, filename);

    if (existsSync(outputPath) && statSync(outputPath).size > 0) {
        console.log(`[${index + 1}/${total}] skipping existing: ${filename}`);
        return;
    }

    console.log(`[${index + 1}/${total}] downloading: ${filename}`);

    const response = await fetch(episode.audioUrl, {
        headers: {
            "User-Agent": "aws-hosted-podcast-migration/1.0",
        },
    });

    if (!response.ok || !response.body) {
        throw new Error(`Failed to download ${episode.audioUrl}: HTTP ${response.status}`);
    }

    await pipeline(response.body, createWriteStream(outputPath));
}

async function main(): Promise<void> {
    mkdirSync(OUTPUT_DIR, { recursive: true });

    console.log(`Fetching RSS feed: ${FEED_URL}`);

    const response = await fetch(FEED_URL, {
        headers: {
            "User-Agent": "aws-hosted-podcast-migration/1.0",
        },
    });

    if (!response.ok) {
        throw new Error(`Failed to fetch feed: HTTP ${response.status}`);
    }

    const feedXml = await response.text();
    const episodes = parseEpisodes(feedXml);

    if (episodes.length === 0) {
        throw new Error("No episodes found. Could not find enclosure URLs in the RSS feed.");
    }

    console.log(`Found ${episodes.length} episodes.`);
    console.log(`Saving to: ${OUTPUT_DIR}`);

    for (let i = 0; i < episodes.length; i += 1) {
        await downloadEpisode(episodes[i], i, episodes.length);
    }

    console.log("Done.");
}

main().catch((error: unknown) => {
    if (error instanceof Error) {
        console.error(error.message);
    } else {
        console.error(error);
    }

    process.exit(1);
});