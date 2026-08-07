/**
 * VK adapter (TypeScript reference).
 *
 * В продакшене бот на Python использует `platforms/vk_adapter.py` с тем же
 * контрактом: photo attachment → max-size URL → Core router (`reference_image_url`).
 */

export type VkPhotoSize = {
  type?: string;
  width?: number;
  height?: number;
  url?: string;
  src?: string;
};

export type VkPhotoAttachment = {
  type: "photo";
  photo?: {
    sizes?: VkPhotoSize[];
    images?: VkPhotoSize[];
  };
};

export type VkInboundMessage = {
  userId: number;
  peerId: number;
  text: string;
  referenceImageUrl?: string;
};

export type PhotoReference = {
  telegramFileId?: string;
  referenceImageUrl?: string;
};

export type PhotoGenerationRequest = {
  platform: "telegram" | "vk";
  userId: number;
  chatId: number;
  prompt: string;
  imageModelId: string;
  imageModelLabel: string;
  photoRef?: PhotoReference;
};

export function pickLargestVkPhotoUrl(sizes: VkPhotoSize[] | undefined | null): string | null {
  if (!sizes?.length) return null;

  let bestUrl: string | null = null;
  let bestArea = -1;
  let bestWidth = -1;

  for (const size of sizes) {
    const url = (size.url ?? size.src)?.trim();
    if (!url?.startsWith("http")) continue;

    const width = size.width ?? 0;
    const height = size.height ?? 0;
    const area = width * height;

    if (area > bestArea || (area === bestArea && width > bestWidth)) {
      bestArea = area;
      bestWidth = width;
      bestUrl = url;
    }
  }

  return bestUrl;
}

export function extractVkPhotoUrl(attachments: VkPhotoAttachment[] | undefined): string | null {
  if (!attachments?.length) return null;

  for (const attachment of attachments) {
    if (attachment.type !== "photo" || !attachment.photo) continue;
    const sizes = attachment.photo.sizes ?? attachment.photo.images ?? [];
    const url = pickLargestVkPhotoUrl(sizes);
    if (url) return url;
  }

  return null;
}

export function normalizeVkMessage(input: {
  fromId: number;
  peerId: number;
  text?: string;
  attachments?: VkPhotoAttachment[];
}): VkInboundMessage {
  return {
    userId: input.fromId,
    peerId: input.peerId,
    text: (input.text ?? "").trim(),
    referenceImageUrl: extractVkPhotoUrl(input.attachments) ?? undefined,
  };
}

/** Сборка payload для Core-роутера (аналог Telegram `telegram_file_id`). */
export function toPhotoGenerationRequest(
  inbound: VkInboundMessage,
  opts: {
    imageModelId: string;
    imageModelLabel: string;
    prompt: string;
    referenceImageUrl?: string;
  },
): PhotoGenerationRequest {
  const refUrl = (opts.referenceImageUrl ?? inbound.referenceImageUrl)?.trim();
  return {
    platform: "vk",
    userId: inbound.userId,
    chatId: inbound.peerId,
    prompt: opts.prompt,
    imageModelId: opts.imageModelId,
    imageModelLabel: opts.imageModelLabel,
    photoRef: refUrl ? { referenceImageUrl: refUrl } : undefined,
  };
}
