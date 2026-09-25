const COLORS = [
    '#3B82F6', '#6366F1', '#8B5CF6', '#EC4899', '#F43F5E',
    '#F97316', '#EAB308', '#22C55E', '#14B8A6', '#06B6D4',
    '#0EA5E9', '#6366F1', '#A855F7', '#D946EF', '#FB7185',
];
function hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = str.charCodeAt(i) + ((hash << 5) - hash);
        hash = hash & hash;
    }
    return Math.abs(hash);
}
export function getAvatarColor(name) {
    if (!name)
        return COLORS[0];
    return COLORS[hashString(name) % COLORS.length];
}
export function getAvatarInitial(name) {
    if (!name)
        return '?';
    return name.charAt(0).toUpperCase();
}
