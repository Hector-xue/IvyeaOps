import axios from "axios";

const api = axios.create({ baseURL: "/api/image-translate", withCredentials: true });

export const listLangs = async () => (await api.get("/langs")).data.langs;
export const listWorkspace = async () => (await api.get("/workspace")).data.images;
export const uploadToWorkspace = async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return (await api.post("/workspace/upload", fd, { headers: { "Content-Type": "multipart/form-data" } })).data;
};
export const deleteWorkspaceImage = async (id) => (await api.delete(`/workspace/${id}`)).data;
export const moveImage = async (id, folder_id) => (await api.post(`/workspace/${id}/move`, { folder_id })).data;

// Folders
export const listFolders = async () => (await api.get("/folders")).data;
export const createFolder = async (name) => (await api.post("/folders", { name })).data;
export const renameFolder = async (id, name) => (await api.patch(`/folders/${id}`, { name })).data;
export const deleteFolder = async (id) => (await api.delete(`/folders/${id}`)).data;

// Translate one source image into many languages. Long-running (gpt-image-2): allow up to 5min per image.
// Batch over multiple images is done by calling this once per image from the UI.
export const translateImage = async (image_id, target_langs) =>
    (await api.post("/translate", { image_id, target_langs }, { timeout: 300000 })).data.results;
