export const ASSET = "/assets/";

// `sea` pairs are sampled from each cover for the website's sea-level illustration.
export const SONGS = [
  { title: "The Rain Song", artist: "Chace", album: "Belated Suffocation", cover: "chace-belated-suffocation.jpg", tone: "chace", lyrics: true,
    sea: ["#4b3421", "#8f6c45"], glow: "#c79a63" },
  { title: "Ibuprofen", artist: "Alicks", album: "Unwritten", cover: "alicks-unwritten.jpg", tone: "alicks", lyrics: true,
    sea: ["#2b302f", "#7c8483"], glow: "#b9c4c2" },
  { title: "Airport Bar", artist: "goodboy noah", album: "Airport Bar", cover: "goodboy-noah-airport-bar.jpg", tone: "noah", lyrics: false,
    sea: ["#a25a30", "#cf9a62"], glow: "#f0a468" },
];

export const coverAlt = song => `${song.artist}《${song.album}》专辑封面`;
