import QtQuick
import QtQuick.VectorImage

Item {
    id: root
    required property QtObject clip

    VectorImage {
        id: art
        objectName: "art"
        anchors.fill: parent
        assumeTrustedSource: true
        preferredRendererType: VectorImage.GeometryRenderer
        animations.loops: Animation.Infinite
    }

    function sync() {
        if (!clip)
            return
        art.source = clip.mediaUrl
        art.animations.paused = clip.hold
        root.opacity = clip.shown
    }

    Component.onCompleted: sync()

    Connections {
        target: root.clip
        function onMediaUrlChanged() { root.sync() }
        function onHoldChanged() { root.sync() }
        function onShownChanged() { root.sync() }
        function onGenerationChanged() { art.animations.restart() }
    }
}
