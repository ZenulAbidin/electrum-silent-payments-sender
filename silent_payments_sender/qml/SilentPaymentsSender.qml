import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtQuick.Controls.Material

import org.electrum 1.0

// Loaded by the Silent Payments Sender plugin into the application context.
//
// Keep this document free of file-based imports ("controls", Electrum
// components): it is compiled synchronously from memory, and compiling a
// dependency from disk at that point can deadlock with Electrum's Python
// translator. Electrum's ConfirmTxDialog is loaded asynchronously instead.
Item {
    id: root
    width: 0
    height: 0

    required property QtObject plugin

    property string recipient: ''
    property var confirmComponent: null

    function closeSendDialog() {
        var mainView = app.stack.getRoot()
        if (mainView && typeof mainView.closeSendDialog === 'function')
            mainView.closeSendDialog()
    }

    function showMessage(title, text) {
        var dialog = app.messageDialog.createObject(app, {
            title: title,
            iconSource: Qt.resolvedUrl('../../icons/warning.png'),
            text: text
        })
        dialog.open()
    }

    function askAmount(amount) {
        amountField.text = amount.satsInt > 0 ? Config.formatSatsForEditing(amount) : ''
        descriptionField.text = ''
        amountDialog.open()
    }

    function withConfirmComponent(callback) {
        if (confirmComponent && confirmComponent.status === Component.Ready) {
            callback(confirmComponent)
            return
        }
        var component = Qt.createComponent(
            Qt.resolvedUrl('ConfirmTxDialog.qml'), Component.Asynchronous)
        function onStatus() {
            if (component.status === Component.Ready) {
                confirmComponent = component
                callback(component)
            } else if (component.status === Component.Error) {
                showMessage(qsTr('Error'), component.errorString())
            }
        }
        if (component.status === Component.Loading)
            component.statusChanged.connect(onStatus)
        else
            onStatus()
    }

    function openConfirm(amountText, description) {
        var finalizer = root.plugin.createFinalizer(
            Daemon.currentWallet,
            Config.unitsToSats(amountText),
            description
        )
        if (!finalizer)
            return
        withConfirmComponent(function(component) {
            var dialog = component.createObject(app, {
                objectName: 'silentPaymentsConfirmDialog',
                title: qsTr('Confirm Silent Payment'),
                finalizer: finalizer,
                satoshis: finalizer.amount
            })
            if (!dialog) {
                showMessage(qsTr('Error'), component.errorString())
                root.plugin.releaseFinalizer(finalizer)
                return
            }
            // Set after creation: the dialog copies these into its finalizer.
            dialog.address = root.recipient
            dialog.message = description
            dialog.amountLabelText = qsTr('Amount to send')
            dialog.sendButtonText = qsTr('Pay...')
            dialog.accepted.connect(function() {
                finalizer.signAndSend()
            })
            dialog.rejected.connect(function() {
                root.plugin.releaseFinalizer(finalizer)
                dialog.destroy()
            })
            finalizer.finished.connect(function(signed, saved, complete) {
                dialog.destroy()
            })
            finalizer.signError.connect(function(message) {
                showMessage(qsTr('Error'),
                    [qsTr('Could not create silent payment'), message].join('\n\n'))
                // A failed candidate is never signed again; start over.
                if (!dialog.visible) {
                    root.plugin.releaseFinalizer(finalizer)
                    dialog.destroy()
                }
            })
            dialog.open()
        })
    }

    Connections {
        target: root.plugin

        function onRequestError(message) {
            closeSendDialog()
            showMessage(qsTr('Silent Payment'), message)
        }

        function onPaymentRequested(recipient, amount, isMainnet) {
            closeSendDialog()
            root.recipient = recipient
            if (!isMainnet) {
                askAmount(amount)
                return
            }
            var warning = app.messageDialog.createObject(app, {
                title: qsTr('Confirm experimental mainnet payment'),
                iconSource: Qt.resolvedUrl('../../icons/warning.png'),
                yesno: true,
                text: [
                    qsTr('This independent Silent Payments plugin has not received a professional security audit. A defect could make the recipient unable to detect or spend the payment.'),
                    qsTr('Verify the sp1 address independently and use a small amount first. Continue with a real-bitcoin transaction?')
                ].join('\n\n')
            })
            warning.accepted.connect(function() {
                askAmount(amount)
            })
            warning.open()
        }
    }

    Dialog {
        id: amountDialog
        objectName: 'silentPaymentsAmountDialog'

        // Mirror Electrum's ElDialog behaviour (placement, back button).
        parent: app.keyboardFreeZone
        modal: true
        Overlay.modal: Rectangle { color: "#aa000000" }
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        width: parent ? parent.width : implicitWidth
        anchors.centerIn: parent
        title: qsTr('Silent Payment')

        // Electrum's back-button handler calls doClose() on the top dialog.
        function doClose() { reject() }

        onOpenedChanged: {
            if (opened) {
                app.activeDialogs.push(amountDialog)
            } else {
                var index = app.activeDialogs.indexOf(amountDialog)
                if (index >= 0)
                    app.activeDialogs.splice(index, 1)
            }
        }

        ColumnLayout {
            width: parent.width
            spacing: constants.paddingSmall

            Label {
                text: qsTr('Recipient')
                color: Material.accentColor
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                font.family: FixedFont
                text: root.recipient
            }

            Label {
                Layout.topMargin: constants.paddingMedium
                text: qsTr('Amount')
                color: Material.accentColor
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: amountField
                    objectName: 'silentPaymentsAmountField'
                    Layout.fillWidth: true
                    font.family: FixedFont
                    placeholderText: qsTr('Amount')
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: RegularExpressionValidator {
                        regularExpression: Config.btcAmountRegex
                    }
                }
                Label {
                    text: Config.baseUnit
                    color: Material.accentColor
                }
            }

            Label {
                Layout.topMargin: constants.paddingMedium
                text: qsTr('Description')
                color: Material.accentColor
            }
            TextField {
                id: descriptionField
                objectName: 'silentPaymentsDescriptionField'
                Layout.fillWidth: true
                placeholderText: qsTr('Description (optional)')
            }

            Label {
                Layout.fillWidth: true
                Layout.topMargin: constants.paddingMedium
                wrapMode: Text.Wrap
                font.pixelSize: constants.fontSizeSmall
                text: qsTr('Silent payments are signed immediately. Unsigned export and fee bumping are disabled because the output depends on the exact inputs.')
            }
        }

        footer: DialogButtonBox {
            Button {
                text: qsTr('Cancel')
                flat: true
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
            Button {
                objectName: 'silentPaymentsNextButton'
                text: qsTr('Next')
                flat: true
                enabled: Config.unitsToSats(amountField.text).satsInt > 0
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
        }

        onAccepted: {
            root.openConfirm(amountField.text, descriptionField.text)
        }
    }
}
